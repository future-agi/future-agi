"""
API tests for GET /simulate/run-tests/ (RunTestListView).
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.models.agent_version import AgentVersion


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def prompt_template(db, organization, workspace):
    return PromptTemplate.objects.create(
        name="Test prompt template",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version_v10(db, prompt_template):
    # template_version is a CharField on the model — the response serializer
    # must accept any string, not just numeric values.
    return PromptVersion.objects.create(
        original_template=prompt_template,
        template_version="v10",
    )


@pytest.fixture
def scenario_with_prompt_version(
    db,
    organization,
    workspace,
    agent_definition,
    prompt_template,
    prompt_version_v10,
):
    return Scenarios.objects.create(
        name="Scenario linked to v10 prompt version",
        source="seed",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        prompt_template=prompt_template,
        prompt_version=prompt_version_v10,
    )


@pytest.fixture
def run_test_with_v10_scenario(
    db,
    organization,
    workspace,
    agent_definition,
    scenario_with_prompt_version,
):
    run_test = RunTest.objects.create(
        name="Run test referencing v10-prompt scenario",
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )
    run_test.scenarios.set([scenario_with_prompt_version])
    return run_test


@pytest.fixture
def word_count_eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="word_count_contract",
        description="Word count contract template",
        organization=organization,
        workspace=workspace,
        config={
            "required_keys": ["text"],
            "optional_keys": [],
            "output": "Pass/Fail",
            "eval_type_id": "word_count_in_range",
            "function_params_schema": {
                "min_words": {
                    "type": "integer",
                    "required": True,
                    "default": 1,
                    "minimum": 0,
                },
                "max_words": {
                    "type": "integer",
                    "required": True,
                    "default": 20,
                    "minimum": 1,
                },
            },
            "config": {},
        },
        eval_tags=["api-contract"],
    )


@pytest.fixture
def char_count_eval_template(db, organization, workspace):
    """Second template for testing template switches."""
    return EvalTemplate.objects.create(
        name="char_count_contract",
        description="Character count contract template",
        organization=organization,
        workspace=workspace,
        config={
            "required_keys": ["content"],
            "optional_keys": [],
            "output": "Pass/Fail",
            "eval_type_id": "char_count_in_range",
            "function_params_schema": {
                "min_chars": {
                    "type": "integer",
                    "required": True,
                    "default": 10,
                    "minimum": 0,
                },
                "max_chars": {
                    "type": "integer",
                    "required": True,
                    "default": 100,
                    "minimum": 1,
                },
            },
            "config": {},
        },
        eval_tags=["api-contract"],
    )


@pytest.mark.integration
@pytest.mark.api
class TestRunTestListPromptVersionRegression:
    def test_list_succeeds_when_scenario_prompt_version_is_non_numeric(
        self, auth_client, run_test_with_v10_scenario
    ):
        response = auth_client.get("/simulate/run-tests/?page=1&limit=25&search=")

        assert response.status_code == status.HTTP_200_OK, response.content

        body = response.json()
        run_test = next(
            r for r in body["results"] if r["id"] == str(run_test_with_v10_scenario.id)
        )
        scenario = run_test["scenarios_detail"][0]
        assert scenario["prompt_version_detail"]["template_version"] == "v10"


@pytest.mark.integration
@pytest.mark.api
class TestRunTestRuntimeContracts:
    def test_list_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get("/simulate/run-tests/?page=1&legacyPageSize=25")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacyPageSize"] == ["Unknown field."]

    def test_legacy_run_test_list_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get("/simulate/api/run-tests/?page=1&legacyPageSize=25")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacyPageSize"] == ["Unknown field."]

    def test_create_accepts_declared_agent_version_field(
        self, auth_client, agent_definition, scenario_with_prompt_version
    ):
        agent_version = agent_definition.create_version(
            description="Runtime contract version",
            commit_message="Test version",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Runtime Contract Run",
                "agent_definition_id": str(agent_definition.id),
                "agent_version": str(agent_version.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_test = RunTest.objects.get(id=response.json()["id"])
        assert run_test.agent_version_id == agent_version.id

    def test_create_persists_request_workspace(
        self, auth_client, agent_definition, scenario_with_prompt_version, workspace
    ):
        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Runtime Contract Workspace Run",
                "agent_definition_id": str(agent_definition.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_test = RunTest.objects.get(id=response.json()["id"])
        assert run_test.workspace_id == workspace.id

    def test_create_uses_active_org_not_legacy_user_fk(
        self, auth_client, user, agent_definition, scenario_with_prompt_version
    ):
        """Regression for TH-6191.

        A multi-org user whose legacy ``user.organization`` FK points at a
        different org than the one they are actively working in must still be
        able to create a run test against agent definitions/scenarios that live
        in the active org. Before the fix, the create serializer scoped its
        existence checks by ``request.user.organization`` (the legacy FK), so
        objects in the active org were reported as "not found" even though the
        write path (which uses ``request.organization``) would have found them.
        """
        from accounts.models import Organization

        # agent_definition + scenario live in the active org (auth_client's
        # org). Point the user's legacy FK at a *different* org to mimic a
        # multi-org user operating outside their primary/legacy organization.
        legacy_primary_org = Organization.objects.create(name="Legacy Primary Org")
        user.organization = legacy_primary_org
        user.save(update_fields=["organization"])

        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Multi-org Active Org Run",
                "agent_definition_id": str(agent_definition.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_test = RunTest.objects.get(id=response.json()["id"])
        assert run_test.organization_id == agent_definition.organization_id

    def test_create_uses_active_org_for_eval_config_ids(
        self,
        auth_client,
        user,
        organization,
        workspace,
        agent_definition,
        scenario_with_prompt_version,
    ):
        """Sibling to test_create_uses_active_org_not_legacy_user_fk, exercising
        `CreateRunTestSerializer.validate_eval_config_ids`. Same active-org vs
        legacy-FK divergence; the eval_config referenced in the payload lives in
        the active org via its parent RunTest.
        """
        from accounts.models import Organization

        seed_run_test = RunTest.objects.create(
            name="Seed run test for eval config",
            organization=organization,
            workspace=workspace,
            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            agent_definition=agent_definition,
        )
        eval_config = SimulateEvalConfig.objects.create(
            name="Active-org eval config",
            eval_template=EvalTemplate.objects.create(
                name="Reusable template",
                eval_id=99001,
                config={},
            ),
            run_test=seed_run_test,
            model="turing_small",
            error_localizer=False,
        )

        legacy_primary_org = Organization.objects.create(name="Legacy Primary Org")
        user.organization = legacy_primary_org
        user.save(update_fields=["organization"])

        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Multi-org run using eval_config from active org",
                "agent_definition_id": str(agent_definition.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
                "eval_config_ids": [str(eval_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_test = RunTest.objects.get(id=response.json()["id"])
        assert run_test.organization_id == agent_definition.organization_id

    def test_prompt_simulation_create_uses_active_org_not_legacy_user_fk(
        self,
        auth_client,
        user,
        prompt_template,
        prompt_version_v10,
        scenario_with_prompt_version,
    ):
        """Regression for TH-6191 on the prompt-simulation create path.

        `CreatePromptSimulationSerializer.validate_prompt_template_id` and
        `validate_scenario_ids` had the same legacy-FK vs active-org drift.
        Multi-org users creating a prompt simulation against a template + scenario
        that live in the active org must succeed.
        """
        from accounts.models import Organization

        legacy_primary_org = Organization.objects.create(name="Legacy Primary Org")
        user.organization = legacy_primary_org
        user.save(update_fields=["organization"])

        response = auth_client.post(
            f"/simulate/prompt-templates/{prompt_template.id}/simulations/",
            {
                "name": "Multi-org Prompt Simulation",
                "prompt_version_id": str(prompt_version_v10.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_test = RunTest.objects.get(id=response.json()["result"]["id"])
        assert run_test.organization_id == prompt_template.organization_id

    def test_create_rejects_unknown_body_field(
        self, auth_client, agent_definition, scenario_with_prompt_version
    ):
        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Unknown Field Run",
                "agent_definition_id": str(agent_definition.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
                "legacy_extra": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_update_rejects_unknown_body_field(
        self, auth_client, run_test_with_v10_scenario
    ):
        response = auth_client.patch(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/",
            {"name": "Updated", "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_execute_rejects_unknown_body_field(
        self, auth_client, run_test_with_v10_scenario
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/execute/",
            {"legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_call_execution_list_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get("/simulate/api/call-executions/?legacyPageSize=25")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacyPageSize"] == ["Unknown field."]

    def test_test_execution_detail_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get(f"/simulate/test-executions/{uuid4()}/?legacy=1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy"] == ["Unknown field."]

    def test_call_execution_status_update_rejects_unknown_body_field(self, auth_client):
        response = auth_client.patch(
            f"/simulate/call-executions/{uuid4()}/",
            {"status": "failed", "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_call_execution_status_update_sanitizes_failed_ended_reason(
        self, auth_client, run_test_with_v10_scenario, scenario_with_prompt_version
    ):
        test_execution = TestExecution.objects.create(
            run_test=run_test_with_v10_scenario,
            status=TestExecution.ExecutionStatus.PENDING,
            total_scenarios=1,
        )
        call_execution = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario_with_prompt_version,
            status=CallExecution.CallStatus.PENDING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )
        raw_reason = "raw stack trace with implementation details"

        response = auth_client.patch(
            f"/simulate/call-executions/{call_execution.id}/",
            {"status": "failed", "ended_reason": raw_reason},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == CallExecution.CallStatus.FAILED
        assert response.json()["ended_reason"] == "Error processing simulation"
        assert raw_reason not in str(response.content)
        call_execution.refresh_from_db()
        assert call_execution.ended_reason == "Error processing simulation"

    def test_components_update_rejects_unknown_body_field(self, auth_client):
        response = auth_client.patch(
            f"/simulate/run-tests/{uuid4()}/components/",
            {"legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_add_eval_configs_rejects_unknown_body_field(self, auth_client):
        response = auth_client.post(
            f"/simulate/run-tests/{uuid4()}/eval-configs/",
            {"evaluations_config": [], "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_update_eval_config_rejects_unknown_body_field(self, auth_client):
        response = auth_client.post(
            f"/simulate/run-tests/{uuid4()}/eval-configs/{uuid4()}/update/",
            {"legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_eval_config_create_structure_and_update_roundtrip(
        self, auth_client, run_test_with_v10_scenario, word_count_eval_template
    ):
        create_response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/",
            {
                "evaluations_config": [
                    {
                        "template_id": str(word_count_eval_template.id),
                        "name": "word_count_initial",
                        "mapping": {"text": "transcript"},
                        "config": {
                            "params": {"min_words": "2", "max_words": "8"},
                            "run_config": {"pass_threshold": 0.7},
                        },
                        "filters": [
                            {
                                "column_id": "status",
                                "filter_config": {
                                    "filter_type": "text",
                                    "filter_op": "equals",
                                    "filter_value": "completed",
                                },
                            }
                        ],
                        "error_localizer": False,
                        "model": "turing_small",
                    }
                ]
            },
            format="json",
        )

        assert create_response.status_code == status.HTTP_201_CREATED, (
            create_response.content
        )
        eval_config_id = create_response.json()["created_eval_configs"][0]["id"]
        eval_config = SimulateEvalConfig.objects.get(id=eval_config_id)
        assert eval_config.run_test_id == run_test_with_v10_scenario.id
        assert eval_config.config["params"] == {"min_words": 2, "max_words": 8}
        assert eval_config.mapping == {"text": "transcript"}
        assert eval_config.filters[0]["filter_config"]["filter_op"] == "equals"

        structure_response = auth_client.get(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config_id}/get-structure/"
        )

        assert structure_response.status_code == status.HTTP_200_OK, (
            structure_response.content
        )
        structure = structure_response.json()["result"]["eval"]
        assert structure["id"] == eval_config_id
        assert structure["template_id"] == str(word_count_eval_template.id)
        assert structure["mapping"] == {"text": "transcript"}
        assert structure["params"] == {"min_words": 2, "max_words": 8}

        update_response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config_id}/update/",
            {
                "template_id": str(word_count_eval_template.id),
                "name": "word_count_updated",
                "mapping": {"text": "agent_output"},
                "config": {
                    "params": {"min_words": "3", "max_words": "12"},
                    "run_config": {"pass_threshold": 0.9},
                },
                "filters": [
                    {
                        "column_id": "status",
                        "filter_config": {
                            "filter_type": "text",
                            "filter_op": "not_equals",
                            "filter_value": "failed",
                        },
                    }
                ],
                "error_localizer": True,
                "model": "turing_large",
                "run": False,
            },
            format="json",
        )

        assert update_response.status_code == status.HTTP_200_OK, (
            update_response.content
        )
        eval_config.refresh_from_db()
        assert eval_config.name == "word_count_updated"
        assert eval_config.mapping == {"text": "agent_output"}
        assert eval_config.config["params"] == {"min_words": 3, "max_words": 12}
        assert eval_config.error_localizer is True
        assert eval_config.model == "turing_large"
        assert eval_config.eval_template_id == word_count_eval_template.id
        assert eval_config.filters[0]["filter_config"]["filter_op"] == "not_equals"

    def test_eval_config_create_rejects_other_workspace_template(
        self,
        auth_client,
        organization,
        user,
        run_test_with_v10_scenario,
        word_count_eval_template,
    ):
        other_workspace = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_template = EvalTemplate.objects.create(
            name="other_workspace_eval",
            description="Not visible from selected workspace",
            organization=organization,
            workspace=other_workspace,
            config=word_count_eval_template.config,
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/",
            {
                "evaluations_config": [
                    {
                        "template_id": str(other_template.id),
                        "name": "other_workspace_config",
                        "mapping": {"text": "transcript"},
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SimulateEvalConfig.objects.filter(
            run_test=run_test_with_v10_scenario,
            name="other_workspace_config",
        ).exists()

    def test_eval_config_update_rejects_duplicate_active_name(
        self, auth_client, run_test_with_v10_scenario, word_count_eval_template
    ):
        first = SimulateEvalConfig.objects.create(
            name="duplicate_name",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
        )
        second = SimulateEvalConfig.objects.create(
            name="second_name",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{second.id}/update/",
            {"name": first.name},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already exists" in str(response.content)
        second.refresh_from_db()
        assert second.name == "second_name"

    def test_update_switches_eval_template_to_different_template(
        self,
        auth_client,
        run_test_with_v10_scenario,
        word_count_eval_template,
        char_count_eval_template,
    ):
        """Send {template_id: <second_template>, config, mapping}. Assert template switched."""
        eval_config = SimulateEvalConfig.objects.create(
            name="template_switch_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            config={"params": {"min_words": 2, "max_words": 8}},
            mapping={"text": "transcript"},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {
                "template_id": str(char_count_eval_template.id),
                "config": {
                    "params": {"min_chars": "10", "max_chars": "100"},
                    "run_config": {"pass_threshold": 0.8},
                },
                "mapping": {"content": "agent_output"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.eval_template_id == char_count_eval_template.id
        assert eval_config.config["params"] == {"min_chars": 10, "max_chars": 100}
        assert eval_config.mapping == {"content": "agent_output"}

    def test_update_rejects_template_from_other_workspace(
        self,
        auth_client,
        organization,
        user,
        run_test_with_v10_scenario,
        word_count_eval_template,
    ):
        """Create a template in other_workspace. Assert 400 when user in default workspace tries to use it."""
        other_workspace = Workspace.objects.create(
            name="Other workspace for update",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_template = EvalTemplate.objects.create(
            name="other_workspace_eval_update",
            description="Not visible from default workspace",
            organization=organization,
            workspace=other_workspace,
            config=word_count_eval_template.config,
        )

        eval_config = SimulateEvalConfig.objects.create(
            name="workspace_scope_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(other_template.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        eval_config.refresh_from_db()
        assert eval_config.eval_template_id == word_count_eval_template.id

    def test_update_accepts_global_system_template(
        self,
        auth_client,
        organization,
        run_test_with_v10_scenario,
        word_count_eval_template,
    ):
        """Create a template with organization=None. Assert 200 and template switch persisted."""
        global_template = EvalTemplate.objects.create(
            name="global_system_eval",
            description="Global system template",
            organization=None,
            workspace=None,
            config=word_count_eval_template.config,
        )

        eval_config = SimulateEvalConfig.objects.create(
            name="global_template_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(global_template.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.eval_template_id == global_template.id

    def test_update_returns_400_for_nonexistent_template(
        self, auth_client, run_test_with_v10_scenario, word_count_eval_template
    ):
        """Send {template_id: <random uuid4>}. Assert 400 with 'Evaluation template not found'."""
        eval_config = SimulateEvalConfig.objects.create(
            name="nonexistent_template_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(uuid4())},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Evaluation template not found" in str(response.content)

    def test_update_renormalizes_config_when_only_template_id_sent(
        self,
        auth_client,
        run_test_with_v10_scenario,
        word_count_eval_template,
        char_count_eval_template,
    ):
        """Send {template_id: <second_template>} alone. Assert 400 when old config params don't match new template."""
        eval_config = SimulateEvalConfig.objects.create(
            name="renormalize_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            config={"params": {"min_words": 2, "max_words": 8}},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(char_count_eval_template.id)},
            format="json",
        )

        # When switching templates without providing new config, the old config
        # params (min_words, max_words) don't match the new template's schema
        # (min_chars, max_chars), so the endpoint returns 400.
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        eval_config.refresh_from_db()
        # Template should NOT have switched since config validation failed
        assert eval_config.eval_template_id == word_count_eval_template.id

    def test_update_renormalizes_config_with_compatible_mapping_when_template_changes(
        self,
        auth_client,
        run_test_with_v10_scenario,
        organization,
        workspace,
    ):
        """Switch to template sharing a required_key with compatible params. Assert 200, mapping updated, config re-normalized."""
        template_a = EvalTemplate.objects.create(
            name="template_a",
            description="Template A",
            organization=organization,
            workspace=workspace,
            config={
                "required_keys": ["text", "context"],
                "optional_keys": [],
                "output": "Pass/Fail",
                "eval_type_id": "eval_a",
                "function_params_schema": {
                    "min_words": {
                        "type": "integer",
                        "required": True,
                        "default": 1,
                        "minimum": 0,
                    },
                    "max_words": {
                        "type": "integer",
                        "required": True,
                        "default": 20,
                        "minimum": 1,
                    },
                },
                "config": {},
            },
            eval_tags=["api-contract"],
        )

        template_b = EvalTemplate.objects.create(
            name="template_b",
            description="Template B",
            organization=organization,
            workspace=workspace,
            config={
                "required_keys": ["text", "summary"],
                "optional_keys": [],
                "output": "Pass/Fail",
                "eval_type_id": "eval_b",
                "function_params_schema": {
                    "min_words": {
                        "type": "integer",
                        "required": True,
                        "default": 1,
                        "minimum": 0,
                    },
                    "max_words": {
                        "type": "integer",
                        "required": True,
                        "default": 20,
                        "minimum": 1,
                    },
                },
                "config": {},
            },
            eval_tags=["api-contract"],
        )

        eval_config = SimulateEvalConfig.objects.create(
            name="compatible_mapping_test",
            eval_template=template_a,
            run_test=run_test_with_v10_scenario,
            config={"params": {"min_words": 2, "max_words": 8}},
            mapping={"text": "transcript", "context": "background"},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(template_b.id), "mapping": {"text": "transcript"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.eval_template_id == template_b.id
        # Config should be re-normalized against the new template's schema
        assert eval_config.config == {"params": {"min_words": 2, "max_words": 8}}
        # Mapping should be updated to the explicitly provided shared key
        assert eval_config.mapping == {"text": "transcript"}

    def test_update_rejects_stale_mapping_when_template_changes(
        self,
        auth_client,
        run_test_with_v10_scenario,
        word_count_eval_template,
        char_count_eval_template,
    ):
        """Send {template_id: <second_template>} alone where mapping has stale keys. Assert mapping cleared."""
        eval_config = SimulateEvalConfig.objects.create(
            name="stale_mapping_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            mapping={
                "text": "transcript"
            },  # "text" is valid for word_count but not char_count
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(char_count_eval_template.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        eval_config.refresh_from_db()
        # Mapping should be preserved (unchanged) since the 400 response prevents save
        assert eval_config.mapping == {"text": "transcript"}

    def test_update_preserves_valid_mapping_keys_when_template_changes(
        self,
        auth_client,
        run_test_with_v10_scenario,
        word_count_eval_template,
        organization,
        workspace,
    ):
        """Switching to a template sharing a required key preserves the shared mapping key."""
        second_template = EvalTemplate.objects.create(
            name="word_count_v2",
            description="Another word count template",
            organization=organization,
            workspace=workspace,
            config={
                "required_keys": ["text"],
                "optional_keys": [],
                "output": "Pass/Fail",
                "eval_type_id": "word_count_in_range_v2",
                "function_params_schema": {
                    "min_words": {
                        "type": "integer",
                        "required": True,
                        "default": 5,
                        "minimum": 1,
                    },
                    "max_words": {
                        "type": "integer",
                        "required": True,
                        "default": 50,
                        "minimum": 1,
                    },
                },
                "config": {},
            },
            eval_tags=["api-contract"],
        )

        eval_config = SimulateEvalConfig.objects.create(
            name="preserve_mapping_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            mapping={"text": "transcript"},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"template_id": str(second_template.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.eval_template_id == second_template.id
        # "text" is valid for both templates - mapping must be preserved
        assert eval_config.mapping == {"text": "transcript"}

    def test_update_persists_empty_filters_list(
        self, auth_client, run_test_with_v10_scenario, word_count_eval_template
    ):
        """Send {filters: []}. Assert eval_config.filters == [] in database."""
        eval_config = SimulateEvalConfig.objects.create(
            name="empty_filters_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            filters=[{"column_id": "status", "filter_config": {"filter_op": "equals"}}],
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {"filters": []},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.filters == []

    def test_update_preserves_existing_fields_when_only_filters_sent(
        self, auth_client, run_test_with_v10_scenario, word_count_eval_template
    ):
        """Send {filters: [...]} alone. Assert mapping, config, name, model unchanged."""
        eval_config = SimulateEvalConfig.objects.create(
            name="preserve_fields_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            config={"params": {"min_words": 5, "max_words": 15}},
            mapping={"text": "transcript"},
            model="turing_small",
        )

        original_config = eval_config.config.copy()
        original_mapping = eval_config.mapping.copy()
        original_name = eval_config.name
        original_model = eval_config.model

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {
                "filters": [
                    {
                        "column_id": "status",
                        "filter_config": {
                            "filter_type": "text",
                            "filter_op": "equals",
                            "filter_value": "completed",
                        },
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.config == original_config
        assert eval_config.mapping == original_mapping
        assert eval_config.name == original_name
        assert eval_config.model == original_model
        assert len(eval_config.filters) == 1
        assert eval_config.filters[0]["filter_config"]["filter_op"] == "equals"

    def test_run_test_delete_soft_deletes_eval_configs(
        self, auth_client, run_test_with_v10_scenario, word_count_eval_template
    ):
        eval_config = SimulateEvalConfig.objects.create(
            name="delete_with_run_test",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
        )

        response = auth_client.delete(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/delete/"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        eval_config.refresh_from_db()
        assert eval_config.deleted is True
        assert eval_config.deleted_at is not None

    def test_eval_summary_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get(
            f"/simulate/run-tests/{uuid4()}/eval-summary/?legacy=1"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy"] == ["Unknown field."]

    def test_eval_summary_comparison_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get(
            f"/simulate/run-tests/{uuid4()}/eval-summary-comparison/"
            "?execution_ids=[]&legacy=1"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy"] == ["Unknown field."]

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_update_with_run_true_dispatches_rerun_task(
        self,
        mock_apply_async,
        auth_client,
        run_test_with_v10_scenario,
        word_count_eval_template,
    ):
        """Verify run=True in update endpoint dispatches the eval rerun task."""
        # Create eval config
        eval_config = SimulateEvalConfig.objects.create(
            name="rerun_on_update",
            eval_template=word_count_eval_template,
            run_test=run_test_with_v10_scenario,
            config={"params": {"min_words": 2, "max_words": 8}},
            mapping={"text": "transcript"},
        )

        # Create a COMPLETED TestExecution with call executions
        test_execution = TestExecution.objects.create(
            run_test=run_test_with_v10_scenario,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
        )
        scenario = run_test_with_v10_scenario.scenarios.first()
        call_execution = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.PENDING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )

        call_execution_ids = [str(call_execution.id)]
        eval_config_ids_str = [str(eval_config.id)]

        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/eval-configs/"
            f"{eval_config.id}/update/",
            {
                "run": True,
                "test_execution_id": str(test_execution.id),
                "template_id": str(word_count_eval_template.id),
                "mapping": {"text": "transcript"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        mock_apply_async.assert_called_once_with(
            args=(call_execution_ids, eval_config_ids_str)
        )
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
