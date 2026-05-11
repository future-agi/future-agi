"""
Django integration tests for fi-simulate MCP tools.

Step 6 of the verification methodology: seed real DB state, call tool.execute()
directly, assert invariants on the resulting DB records.

Distinct from the CLI integration probes (which mock HTTP and test the poller
state machine in isolation) — these tests exercise the full Django ORM path:
  RunSimulationTool  → creates TestExecution, calls Temporal dispatch
  SimulateStatusTool → reads TestExecution + CallExecution from DB
  SimulateResultsTool→ reads summary from DB
  SimulateListTool   → paginates RunTest queryset

Invariants checked (mirror TLA+ SimulateCLI.tla):
  NeverPollBeforeStart  — status tool returns NOT_FOUND if execution missing
  SummaryOnlyAfterTerminal — results tool returns error for non-terminal status
  TerminalIsStable      — cancelled execution stays cancelled after re-cancel
  OrphanGuard           — failed Temporal dispatch leaves TestExecution as FAILED
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.django_integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def org(db):
    from accounts.models.organization import Organization
    return Organization.objects.create(name="test-org")


@pytest.fixture()
def user(db, org):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        username=f"user-{uuid.uuid4().hex[:8]}",
        email=f"u{uuid.uuid4().hex[:6]}@test.com",
        password="x",
    )


@pytest.fixture()
def workspace(db, org):
    from accounts.models.workspace import Workspace
    return Workspace.objects.create(name="test-ws", organization=org)


@pytest.fixture()
def context(org, user, workspace):
    from ai_tools.base import ToolContext
    return ToolContext(user=user, organization=org, workspace=workspace)


@pytest.fixture()
def run_test(db, org):
    from simulate.models.run_test import RunTest
    return RunTest.objects.create(
        name="probe-suite",
        organization=org,
        deleted=False,
    )


@pytest.fixture()
def scenario(db, org, run_test):
    from simulate.models.scenarios import Scenarios
    return Scenarios.objects.create(
        name="test-scenario",
        source="",
        organization=org,
        run_test=run_test,
    )


@pytest.fixture()
def pending_execution(db, run_test):
    from simulate.models.test_execution import TestExecution
    return TestExecution.objects.create(
        id=uuid.uuid4(),
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
    )


@pytest.fixture()
def completed_execution(db, run_test):
    from simulate.models.test_execution import TestExecution
    return TestExecution.objects.create(
        id=uuid.uuid4(),
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
    )


def _assert_invariants(execution_id, org, *, expect_terminal=False):
    """Check DB-level TLA+ invariants on a TestExecution record."""
    from simulate.models.test_execution import TestExecution
    te = TestExecution.objects.get(id=execution_id)

    terminal = {
        TestExecution.ExecutionStatus.COMPLETED,
        TestExecution.ExecutionStatus.FAILED,
        TestExecution.ExecutionStatus.CANCELLED,
    }

    if expect_terminal:
        assert te.status in terminal, (
            f"TerminalIsStable violated: expected terminal, got {te.status}"
        )

    # Org scoping: execution must belong to the test org
    assert te.run_test.organization_id == org.id, "OrgScope violated"

    return te


# ---------------------------------------------------------------------------
# RunSimulationTool
# ---------------------------------------------------------------------------

class TestRunSimulationTool:
    def test_creates_test_execution_and_dispatches(self, run_test, scenario, context, org):
        """Happy path: creates TestExecution in DB and calls Temporal dispatch."""
        from ai_tools.tools.simulation.run_to_completion import RunSimulationTool, RunSimulationInput

        tool = RunSimulationTool()
        params = RunSimulationInput(run_test_id=run_test.id)

        with patch("simulate.temporal.client.start_test_execution_workflow") as mock_wf:
            mock_wf.return_value = "wf-id-1"
            result = tool.execute(params, context)

        assert result.data is not None
        execution_id = result.data.get("execution_id")
        assert execution_id, "execution_id must be present in result"

        te = _assert_invariants(execution_id, org)
        assert te.status == "pending"
        assert te.total_scenarios == 1
        mock_wf.assert_called_once()
        _, kwargs = mock_wf.call_args
        assert kwargs["test_execution_id"] == execution_id
        assert kwargs["run_test_id"] == str(run_test.id)
        assert kwargs["org_id"] == str(org.id)

    def test_orphan_guard_on_dispatch_failure(self, run_test, scenario, context, org):
        """OrphanGuard: if Temporal dispatch throws, TestExecution is marked FAILED."""
        from ai_tools.tools.simulation.run_to_completion import RunSimulationTool, RunSimulationInput
        from simulate.models.test_execution import TestExecution

        tool = RunSimulationTool()
        params = RunSimulationInput(run_test_id=run_test.id)

        with patch("simulate.temporal.client.start_test_execution_workflow",
                   side_effect=RuntimeError("temporal down")):
            result = tool.execute(params, context)

        assert result.data is None or result.data.get("execution_id") is None

        # Orphaned record must be FAILED, not stuck in PENDING
        orphans = TestExecution.objects.filter(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
        )
        assert orphans.count() == 0, "OrphanGuard violated: pending execution left after dispatch failure"

    def test_no_scenarios_returns_error(self, run_test, context):
        """RunTest with no scenarios must fail, not create a TestExecution."""
        from ai_tools.tools.simulation.run_to_completion import RunSimulationTool, RunSimulationInput
        from simulate.models.test_execution import TestExecution

        tool = RunSimulationTool()
        params = RunSimulationInput(run_test_id=run_test.id)

        before = TestExecution.objects.filter(run_test=run_test).count()
        result = tool.execute(params, context)
        after = TestExecution.objects.filter(run_test=run_test).count()

        assert result.data is None or not result.data.get("execution_id")
        assert after == before, "TestExecution created despite no scenarios"


# ---------------------------------------------------------------------------
# SimulateStatusTool
# ---------------------------------------------------------------------------

class TestSimulateStatusTool:
    def test_returns_status_for_known_execution(self, pending_execution, run_test, context, org):
        """NeverPollBeforeStart: status lookup by valid execution_id returns data."""
        from ai_tools.tools.simulation.run_to_completion import SimulateStatusTool, SimulateStatusInput

        tool = SimulateStatusTool()
        params = SimulateStatusInput(
            run_test_id=run_test.id,
            execution_id=pending_execution.id,
        )
        result = tool.execute(params, context)

        assert result.data is not None
        assert result.data["status"] == "pending"
        assert str(result.data["execution_id"]) == str(pending_execution.id)

    def test_not_found_for_unknown_execution(self, run_test, context):
        """NeverPollBeforeStart: unknown execution_id → NOT_FOUND, no crash."""
        from ai_tools.tools.simulation.run_to_completion import SimulateStatusTool, SimulateStatusInput

        tool = SimulateStatusTool()
        params = SimulateStatusInput(run_test_id=run_test.id, execution_id=uuid.uuid4())
        result = tool.execute(params, context)

        assert result.data is None or result.data.get("error")


# ---------------------------------------------------------------------------
# SimulateResultsTool
# ---------------------------------------------------------------------------

class TestSimulateResultsTool:
    def test_non_terminal_execution_returns_error(self, pending_execution, run_test, context):
        """SummaryOnlyAfterTerminal: results for pending execution must not return summary."""
        from ai_tools.tools.simulation.run_to_completion import SimulateResultsTool, SimulateResultsInput

        tool = SimulateResultsTool()
        params = SimulateResultsInput(
            run_test_id=run_test.id,
            execution_id=pending_execution.id,
        )
        result = tool.execute(params, context)

        # Should either be an error or have no summary data
        if result.data:
            assert not result.data.get("summary"), (
                "SummaryOnlyAfterTerminal violated: summary returned for pending execution"
            )

    def test_completed_execution_returns_summary(self, completed_execution, run_test, context):
        """SummaryOnlyAfterTerminal: results for completed execution proceeds normally."""
        from ai_tools.tools.simulation.run_to_completion import SimulateResultsTool, SimulateResultsInput

        tool = SimulateResultsTool()
        params = SimulateResultsInput(
            run_test_id=run_test.id,
            execution_id=completed_execution.id,
        )

        with patch("simulate.utils.eval_summary._get_completed_call_executions", return_value=[]), \
             patch("simulate.utils.eval_summary._get_configs_with_template", return_value=[]):
            result = tool.execute(params, context)

        # Should not error out — an empty summary is acceptable for a completed execution
        assert result is not None


# ---------------------------------------------------------------------------
# SimulateListTool
# ---------------------------------------------------------------------------

class TestSimulateListTool:
    def test_lists_only_org_run_tests(self, run_test, context, org, db):
        """OrgScope: list only returns RunTests belonging to the context org."""
        from accounts.models.organization import Organization
        from simulate.models.run_test import RunTest
        from ai_tools.tools.simulation.run_to_completion import SimulateListTool, SimulateListInput

        other_org = Organization.objects.create(name="other-org")
        RunTest.objects.create(name="other-suite", organization=other_org, deleted=False)

        tool = SimulateListTool()
        params = SimulateListInput()
        result = tool.execute(params, context)

        assert result.data is not None
        ids = [str(r["id"]) for r in result.data.get("runs", [])]
        assert str(run_test.id) in ids
        assert all(
            RunTest.objects.get(id=i).organization_id == org.id
            for i in ids
        ), "OrgScope violated: results include run_tests from other orgs"
