"""
Unit tests for the RL harness models in the simulate app.

Tests cover:
- RLEnvironment, RLContract, RLEnvironmentMessage, RLWorld, RLScenario,
  RLWorldCopy: defaults, required fields, unique constraints, soft delete
- RunTest.rl_environment / rl_world: SET_NULL behavior on hard delete
- SimulateEvalConfig: conditional unique constraint on (run_test, name)
- _store_reported_evaluations reusing an existing config by name
- CreateRunTestView rejecting duplicate evaluations_config names
"""

import uuid

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RLContract,
    RLEnvironment,
    RLEnvironmentMessage,
    RLScenario,
    RLWorld,
    RLWorldCopy,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
)

# Aliased: pytest's `Test*` collection pattern would otherwise try to
# collect this Django model as a test class.
from simulate.models import TestExecution as TestExecutionModel
from simulate.services.alk_simulate_ingestion import (
    _REPORTED_EVAL_TEMPLATE,
    _store_reported_evaluations,
)
from tfc.middleware.workspace_context import clear_workspace_context

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def environment(db, organization, workspace):
    """Create a test RL environment."""
    return RLEnvironment.objects.create(
        organization=organization,
        workspace=workspace,
        title="Test RL Environment",
    )


# ============================================================================
# RLEnvironment Model Tests
# ============================================================================


@pytest.mark.unit
class TestRLEnvironmentModel:
    def test_environment_defaults(self, db, organization, workspace):
        """A fresh environment starts idle/understand with no run config."""
        environment = RLEnvironment.objects.create(
            organization=organization,
            workspace=workspace,
            title="New Environment",
        )

        assert environment.status == RLEnvironment.Status.IDLE
        assert environment.phase == RLEnvironment.Phase.UNDERSTAND
        assert environment.deleted is False
        assert environment.run_config == {}

    def test_environment_requires_workspace(self, db, organization):
        """Internal-service writes set no context, so workspace is never
        auto-assigned; a NULL workspace must be rejected at the DB level."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLEnvironment.objects.create(
                    organization=organization,
                    title="No Workspace",
                )


# ============================================================================
# RLContract Model Tests
# ============================================================================


@pytest.mark.unit
class TestRLContractModel:
    def test_contract_version_unique_per_environment(
        self, db, organization, workspace, environment
    ):
        RLContract.objects.create(
            organization=organization, environment=environment, version=1
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLContract.objects.create(
                    organization=organization, environment=environment, version=1
                )

        other_environment = RLEnvironment.objects.create(
            organization=organization,
            workspace=workspace,
            title="Other Environment",
        )
        # Same version number, different environment: unaffected.
        RLContract.objects.create(
            organization=organization, environment=other_environment, version=1
        )

    def test_soft_delete_frees_unique(self, db, organization, environment):
        contract = RLContract.objects.create(
            organization=organization, environment=environment, version=1
        )
        contract.delete()

        recreated = RLContract.objects.create(
            organization=organization, environment=environment, version=1
        )

        assert recreated.id != contract.id
        assert (
            RLContract.all_objects.filter(environment=environment, version=1).count()
            == 2
        )


# ============================================================================
# RLEnvironmentMessage Model Tests
# ============================================================================


@pytest.mark.unit
class TestRLEnvironmentMessageModel:
    def test_message_turn_idempotency_key(self, db, organization, environment):
        turn_id = uuid.uuid4()
        RLEnvironmentMessage.objects.create(
            organization=organization,
            environment=environment,
            turn_id=turn_id,
            seq=1,
            role=RLEnvironmentMessage.Role.USER,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLEnvironmentMessage.objects.create(
                    organization=organization,
                    environment=environment,
                    turn_id=turn_id,
                    seq=2,
                    role=RLEnvironmentMessage.Role.USER,
                )


# ============================================================================
# RLWorld Model Tests
# ============================================================================


@pytest.mark.unit
class TestRLWorldModel:
    def test_world_unique_version_and_snapshot_roundtrip(
        self, db, organization, environment
    ):
        contract = RLContract.objects.create(
            organization=organization, environment=environment, version=1
        )
        snapshot = {"rows": {"users": [{"id": 1}]}, "counters": {"users": 1}}

        world = RLWorld.objects.create(
            organization=organization,
            environment=environment,
            contract=contract,
            version=1,
            snapshot=snapshot,
        )

        reloaded = RLWorld.objects.get(id=world.id)
        assert reloaded.snapshot == snapshot

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLWorld.objects.create(
                    organization=organization,
                    environment=environment,
                    contract=contract,
                    version=1,
                )


# ============================================================================
# RLScenario Model Tests
# ============================================================================


@pytest.mark.unit
class TestRLScenarioModel:
    def test_scenario_name_unique_and_gate_defaults(self, db, organization, environment):
        contract = RLContract.objects.create(
            organization=organization, environment=environment, version=1
        )
        world = RLWorld.objects.create(
            organization=organization,
            environment=environment,
            contract=contract,
            version=1,
        )

        scenario = RLScenario.objects.create(
            organization=organization,
            environment=environment,
            world=world,
            name="Refund flow",
        )
        assert scenario.gate_status == RLScenario.GateStatus.UNPROVEN
        assert scenario.proved_at is None

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLScenario.objects.create(
                    organization=organization,
                    environment=environment,
                    world=world,
                    name="Refund flow",
                )


# ============================================================================
# RLWorldCopy Model Tests
# ============================================================================


@pytest.mark.unit
class TestRLWorldCopyModel:
    def test_world_copy_token_unique_and_call_execution_guard(
        self, db, organization, environment
    ):
        contract = RLContract.objects.create(
            organization=organization, environment=environment, version=1
        )
        world = RLWorld.objects.create(
            organization=organization,
            environment=environment,
            contract=contract,
            version=1,
        )
        scenario = RLScenario.objects.create(
            organization=organization,
            environment=environment,
            world=world,
            name="Refund flow",
        )

        run_test = RunTest.objects.create(organization=organization, name="Copy Run")
        test_scenario = Scenarios.objects.create(
            organization=organization, name="Copy Scenario", source="src"
        )
        test_execution = TestExecutionModel.objects.create(run_test=run_test)
        call_execution = CallExecution.objects.create(
            test_execution=test_execution, scenario=test_scenario
        )

        first_copy = RLWorldCopy.objects.create(
            organization=organization,
            environment=environment,
            world=world,
            scenario=scenario,
            call_execution=call_execution,
            purpose=RLWorldCopy.Purpose.GATE,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLWorldCopy.objects.create(
                    organization=organization,
                    environment=environment,
                    world=world,
                    scenario=scenario,
                    call_execution=call_execution,
                    purpose=RLWorldCopy.Purpose.GATE,
                )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RLWorldCopy.objects.create(
                    organization=organization,
                    environment=environment,
                    world=world,
                    scenario=scenario,
                    token=first_copy.token,
                    purpose=RLWorldCopy.Purpose.GATE,
                )

        # call_execution=None is excluded from the constraint's condition.
        RLWorldCopy.objects.create(
            organization=organization,
            environment=environment,
            world=world,
            scenario=scenario,
            purpose=RLWorldCopy.Purpose.GATE,
        )
        RLWorldCopy.objects.create(
            organization=organization,
            environment=environment,
            world=world,
            scenario=scenario,
            purpose=RLWorldCopy.Purpose.GATE,
        )


# ============================================================================
# RunTest RL Foreign Key Tests
# ============================================================================


@pytest.mark.unit
class TestRunTestRLForeignKeys:
    def test_run_test_fk_null_on_hard_delete(self, db, organization, environment):
        run_test = RunTest.objects.create(
            organization=organization, name="RL Linked Run", rl_environment=environment
        )

        # Bypass soft delete: a real DB-level DELETE via the queryset.
        RLEnvironment.all_objects.filter(id=environment.id).delete()

        run_test.refresh_from_db()
        assert run_test.rl_environment is None


# ============================================================================
# SimulateEvalConfig Constraint Tests
# ============================================================================


@pytest.mark.unit
class TestSimulateEvalConfigConstraint:
    def test_eval_config_name_unique_per_run_test(self, db, organization):
        template = EvalTemplate.objects.create(
            name="Eval Config Constraint Template", organization=organization, config={}
        )
        run_test = RunTest.objects.create(organization=organization, name="Run A")

        SimulateEvalConfig.objects.create(
            run_test=run_test, name="Sub-goal", eval_template=template
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SimulateEvalConfig.objects.create(
                    run_test=run_test, name="Sub-goal", eval_template=template
                )

        # Same name, different run_test: unaffected.
        other_run_test = RunTest.objects.create(organization=organization, name="Run B")
        SimulateEvalConfig.objects.create(
            run_test=other_run_test, name="Sub-goal", eval_template=template
        )

        # name=None is excluded from the constraint's condition.
        SimulateEvalConfig.objects.create(
            run_test=run_test, name=None, eval_template=template
        )
        SimulateEvalConfig.objects.create(
            run_test=run_test, name=None, eval_template=template
        )


# ============================================================================
# _store_reported_evaluations Reuse Tests
# ============================================================================


@pytest.mark.unit
class TestReportedEvaluationsReuse:
    def test_reported_evaluations_reuses_existing_config(self, db, organization):
        run_test = RunTest.objects.create(
            organization=organization, name="Reported Eval Run"
        )
        scenario = Scenarios.objects.create(
            organization=organization, name="Reported Eval Scenario", source="src"
        )
        test_execution = TestExecutionModel.objects.create(run_test=run_test)
        call_execution = CallExecution.objects.create(
            test_execution=test_execution, scenario=scenario
        )

        # Active workspace context would silently reassign organization=None
        # back to the current org, defeating the organization__isnull lookup
        # _store_reported_evaluations depends on to find the global template.
        clear_workspace_context()
        template = EvalTemplate.objects.create(
            name=_REPORTED_EVAL_TEMPLATE, organization=None, config={}
        )
        config = SimulateEvalConfig.objects.create(
            run_test=run_test, name="Sub-goal A", eval_template=template
        )

        _store_reported_evaluations(
            call_execution,
            [{"name": "Sub-goal A", "passed": True, "reason": "matched"}],
        )

        assert (
            SimulateEvalConfig.objects.filter(
                run_test=run_test, name="Sub-goal A"
            ).count()
            == 1
        )
        output = call_execution.eval_outputs[str(config.id)]
        assert output["output"]["choice"] == "Passed"


# ============================================================================
# CreateRunTestView Duplicate Eval Name Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCreateRunTestRejectsDuplicateEvalNames:
    def test_create_run_test_rejects_duplicate_eval_names(
        self, auth_client, organization, workspace
    ):
        agent_definition = AgentDefinition.objects.create(
            agent_name="Duplicate Eval Names Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        scenario = Scenarios.objects.create(
            name="Duplicate Eval Names Scenario",
            source="src",
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
        )
        template = EvalTemplate.objects.create(
            name="Duplicate Eval Names Template", organization=organization, config={}
        )

        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Duplicate Eval Names Run",
                "agent_definition_id": str(agent_definition.id),
                "scenario_ids": [str(scenario.id)],
                "evaluations_config": [
                    {"template_id": str(template.id), "name": "Sub-goal A"},
                    {"template_id": str(template.id), "name": "Sub-goal A"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not RunTest.objects.filter(name="Duplicate Eval Names Run").exists()
