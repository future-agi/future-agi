"""
Tests for the call-log ingestion helper and the Logs reader endpoint.

Covers TH-4335:
- `_ingest_call_logs` extraction preserves the behaviour of the legacy
  `ingest_call_logs_task` (rows persisted, summary updated, source respected).
- `ingest_call_logs_task` (the Temporal wrapper) still forwards its args to
  the helper so the legacy `TestExecutor` code path keeps working.
- `CallExecutionLogsView` returns HTTP 200 with an empty paginated page
  when the call has no logs (previously returned 400).
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, CallLogEntry, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
try:
    from ee.voice.tasks.call_log_tasks import _ingest_call_logs, ingest_call_logs_task
except ImportError:
    _ingest_call_logs = None
    ingest_call_logs_task = None


# ============================================================================
# Fixtures — minimal call_execution with full ancestry
# ============================================================================


@pytest.fixture
def keep_test_db_connection_open():
    """The helper closes stale worker connections in production; keep pytest's
    transaction connection open while exercising the helper directly."""
    with (
        patch("ee.voice.tasks.call_log_tasks.close_old_connections", return_value=None),
        patch("tfc.temporal.drop_in.decorator.close_old_connections", return_value=None),
    ):
        yield


@pytest.fixture(autouse=True)
def _keep_test_db_connection_open(keep_test_db_connection_open):
    yield


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Test Simulator",
        prompt="You are a simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()

    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="Test situation")
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="desc",
        source="src",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="Test Run",
        description="desc",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-123",
        call_metadata={"call_direction": "outbound"},
    )


# ============================================================================
# _ingest_call_logs — helper unit tests
# ============================================================================


def _fake_log_payload(body, severity="INFO", category="llm", ts_ms=1_700_000_000_000):
    """Build a VAPI-shaped log payload dict."""
    return {
        "time": ts_ms,
        "level": 30,
        "severityText": severity,
        "body": body,
        "attributes": {"category": category},
    }


@pytest.mark.django_db
class TestIngestCallLogsHelper:
    """_ingest_call_logs — plain-Python helper extracted from the Temporal
    task so activities can run ingestion inline without chaining a second
    Temporal activity."""

    def test_persists_rows_and_summary_for_customer_source(self, call_execution):
        payloads = [
            _fake_log_payload("first line"),
            _fake_log_payload("second line", severity="WARN", category="model"),
        ]
        with patch("ee.voice.tasks.call_log_tasks.VoiceServiceManager") as MockVSM:
            MockVSM.return_value.iter_call_logs.return_value = iter(payloads)

            ok = _ingest_call_logs(
                str(call_execution.id),
                "https://example.com/log",
                source=CallLogEntry.LogSource.CUSTOMER,
            )

        assert ok is True
        rows = CallLogEntry.objects.filter(call_execution=call_execution)
        assert rows.count() == 2
        assert set(rows.values_list("source", flat=True)) == {
            CallLogEntry.LogSource.CUSTOMER
        }

        call_execution.refresh_from_db()
        assert call_execution.customer_logs_summary["total_entries"] == 2

    def test_customer_vs_agent_summary_field(self, call_execution):
        with patch("ee.voice.tasks.call_log_tasks.VoiceServiceManager") as MockVSM:
            MockVSM.return_value.iter_call_logs.return_value = iter(
                [_fake_log_payload("x")]
            )
            _ingest_call_logs(
                str(call_execution.id),
                "https://example.com/log",
                source=CallLogEntry.LogSource.AGENT,
            )

        call_execution.refresh_from_db()
        # AGENT source populates logs_summary (not customer_logs_summary)
        assert call_execution.logs_summary == {
            "total_entries": 1,
            "level_counts": {"30": 1},
            "category_counts": {"llm": 1},
            "last_logged_at": call_execution.logs_summary["last_logged_at"],
        }
        assert not call_execution.customer_logs_summary

    def test_missing_call_execution_returns_false_without_raising(self, db):
        # Non-existent call id should be handled gracefully (False return)
        # so the caller's own persistence doesn't get rolled back.
        ok = _ingest_call_logs(
            "00000000-0000-0000-0000-000000000000",
            "https://example.com/log",
            source=CallLogEntry.LogSource.CUSTOMER,
        )
        assert ok is False


# ============================================================================
# ingest_call_logs_task — legacy wrapper forwards to helper
# ============================================================================


@pytest.mark.django_db
def test_ingest_call_logs_task_delegates_to_helper(call_execution):
    """The Temporal-decorated wrapper must remain a thin pass-through so the
    legacy TestExecutor code path continues working. Run it on an empty
    iterator — the wrapper's only job is to forward args to the helper."""
    with patch("ee.voice.tasks.call_log_tasks.VoiceServiceManager") as MockVSM:
        MockVSM.return_value.iter_call_logs.return_value = iter([])
        ok = ingest_call_logs_task(
            str(call_execution.id),
            "https://example.com/log",
            verify_ssl=False,
            source=CallLogEntry.LogSource.CUSTOMER,
        )

    assert ok is True
    MockVSM.return_value.iter_call_logs.assert_called_once_with(
        url="https://example.com/log",
        verify_ssl=False,
    )


# ============================================================================
# CallExecutionLogsView — empty result returns 200, not 400
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCallExecutionLogsViewEmpty:
    """Regression guard for TH-4335: the reader used to return HTTP 400
    ("No logs found") on an empty queryset, which the frontend can't tell
    apart from a real error. Empty now returns 200 with an empty page."""

    URL_TEMPLATE = "/simulate/call-executions/{}/logs/"

    def test_returns_200_with_empty_results_when_no_logs(
        self, auth_client, call_execution
    ):
        url = self.URL_TEMPLATE.format(call_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        # Paginated response shape: {count, next, previous, results: {...}}
        assert payload.get("count", 0) == 0
        # The inner `results` dict carries the logs array + source marker
        inner = payload.get("results") or {}
        assert inner.get("results") == []
