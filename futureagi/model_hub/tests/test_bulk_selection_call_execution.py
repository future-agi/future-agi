"""Phase 8 — CallExecution resolver tests."""

from __future__ import annotations

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.services.bulk_selection import (
    ResolveResult,
    resolve_filtered_call_execution_ids,
)
from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import CallExecution, TestExecution


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _make_agent_def(*, organization, workspace=None, name="ce-agent"):
    """Minimal AgentDefinition satisfying the required-field invariants."""
    return AgentDefinition.objects.create(
        agent_name=name,
        inbound=True,
        description="fixture agent",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def agent_def(db, organization, workspace):
    return _make_agent_def(organization=organization, workspace=workspace)


@pytest.fixture
def run_test(db, organization, agent_def):
    return RunTest.objects.create(
        name="ce-run-test",
        organization=organization,
    )


@pytest.fixture
def test_execution(db, run_test, agent_def):
    return TestExecution.objects.create(
        run_test=run_test,
        agent_definition=agent_def,
    )


@pytest.fixture
def scenario(db, organization, workspace):
    return Scenarios.objects.create(
        name="ce-scenario",
        source="test source",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def seeded_call_executions(db, test_execution, scenario):
    """12 call executions attached to the single test_execution."""
    return [
        CallExecution.objects.create(
            test_execution=test_execution, scenario=scenario
        )
        for _ in range(12)
    ]


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestBaseline:
    def test_no_filter_returns_all_under_agent_def(
        self, agent_def, seeded_call_executions, organization
    ):
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
        )
        assert isinstance(result, ResolveResult)
        assert result.total_matching == 12
        assert len(result.ids) == 12
        assert result.truncated is False

    def test_none_filters_equivalent_to_empty(
        self, agent_def, seeded_call_executions, organization
    ):
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=None,  # type: ignore[arg-type]
            organization=organization,
        )
        assert result.total_matching == 12


# --------------------------------------------------------------------------
# exclude_ids
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestExcludeIds:
    def test_excludes_given_ids_from_result(
        self, agent_def, seeded_call_executions, organization
    ):
        exclude = {seeded_call_executions[0].id, seeded_call_executions[1].id}
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            exclude_ids=exclude,
            organization=organization,
        )
        assert result.total_matching == 10
        for excluded_id in exclude:
            assert excluded_id not in result.ids

    def test_exclude_accepts_list_and_tuple(
        self, agent_def, seeded_call_executions, organization
    ):
        list_result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            exclude_ids=[seeded_call_executions[0].id],
            organization=organization,
        )
        assert list_result.total_matching == 11

        tuple_result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            exclude_ids=(seeded_call_executions[1].id,),
            organization=organization,
        )
        assert tuple_result.total_matching == 11

    def test_exclude_none_is_noop(
        self, agent_def, seeded_call_executions, organization
    ):
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            exclude_ids=None,
            organization=organization,
        )
        assert result.total_matching == 12


# --------------------------------------------------------------------------
# Cap enforcement
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestCap:
    def test_cap_truncates_ids(
        self, agent_def, seeded_call_executions, organization
    ):
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
            cap=5,
        )
        assert len(result.ids) == 5
        # Capped resolvers return a cap+1 sentinel instead of a precise count.
        assert result.total_matching == 6
        assert result.truncated is True

    def test_cap_above_total_is_not_truncated(
        self, agent_def, seeded_call_executions, organization
    ):
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
            cap=100,
        )
        assert result.truncated is False
        assert len(result.ids) == 12

    def test_cap_returns_most_recent_first(
        self, agent_def, seeded_call_executions, organization
    ):
        """Newest-created first. Last seeded row has the latest created_at."""
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
            cap=3,
        )
        assert result.ids[0] == seeded_call_executions[-1].id


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsolation:
    def test_org_isolation_returns_empty(
        self, agent_def, seeded_call_executions, organization, db
    ):
        """CallExecutions from another org must not appear when scoped to ours."""
        other_org = Organization.objects.create(name="Other CE Org")
        other_agent_def = _make_agent_def(
            organization=other_org, workspace=None, name="other-agent"
        )
        other_run_test = RunTest.objects.create(
            name="other-run", organization=other_org
        )
        other_te = TestExecution.objects.create(
            run_test=other_run_test, agent_definition=other_agent_def
        )
        other_scenario = Scenarios.objects.create(
            name="other-scenario",
            source="other source",
            organization=other_org,
        )
        other_ce = CallExecution.objects.create(
            test_execution=other_te, scenario=other_scenario
        )

        # Caller from the default org, querying their own agent_def should
        # not see the other org's CE.
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
        )
        assert other_ce.id not in result.ids
        assert result.total_matching == 12

        # And the reverse — querying other_agent_def from our org returns
        # empty because the run_test belongs to another org.
        result2 = resolve_filtered_call_execution_ids(
            project_id=other_agent_def.id,
            filters=[],
            organization=organization,
        )
        assert result2.total_matching == 0

    def test_workspace_isolation(
        self, agent_def, seeded_call_executions, organization, workspace, user, db
    ):
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
            workspace=other_ws,
        )
        assert result.total_matching == 0
        assert result.ids == []

    def test_agent_definition_scoping(
        self, agent_def, seeded_call_executions, organization, workspace, scenario, db
    ):
        """CEs from a different agent_def (same org) are excluded."""
        other_agent_def = _make_agent_def(
            organization=organization, workspace=workspace, name="sibling-agent"
        )
        other_run = RunTest.objects.create(
            name="sibling-run", organization=organization
        )
        other_te = TestExecution.objects.create(
            run_test=other_run, agent_definition=other_agent_def
        )
        sibling_ce = CallExecution.objects.create(
            test_execution=other_te, scenario=scenario
        )

        result = resolve_filtered_call_execution_ids(
            project_id=agent_def.id,
            filters=[],
            organization=organization,
        )
        assert sibling_ce.id not in result.ids
        assert result.total_matching == 12
