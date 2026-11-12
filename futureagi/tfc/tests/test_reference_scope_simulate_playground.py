"""Simulate and agent playground handlers resolve referenced ids in scope.

Two synthetic tenants. Rejection tests send the other tenant's id and
compare with an unknown id; controls send the caller's own ids. Workflow
starts and external calls are mocked.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from agent_playground.models.choices import GraphVersionStatus, NodeType
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.models.persona import Persona
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant, same_answer

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def tenants(db, settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    return make_tenant("A"), make_tenant("B")


def _post(client, url, payload):
    clear_workspace_context()
    return client.post(url, payload, format="json")


def _eval_group(tenant):
    clear_workspace_context()
    return EvalGroup.no_workspace_objects.create(
        name=f"group-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        created_by=tenant.user,
    )


def _kb(tenant):
    clear_workspace_context()
    return KnowledgeBaseFile.no_workspace_objects.create(
        name=f"kb-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
    )


def _own_dataset(tenant):
    from model_hub.models.choices import DatasetSourceChoices
    from model_hub.models.develop_dataset import Dataset

    clear_workspace_context()
    return Dataset.no_workspace_objects.create(
        name=f"scenario-data-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


def _scenario(tenant):
    from simulate.models import Scenarios

    clear_workspace_context()
    return Scenarios.no_workspace_objects.create(
        name=f"scenario-{uuid.uuid4().hex[:6]}",
        source="synthetic",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=tenant.organization,
        workspace=tenant.workspace,
    )


def _agent(tenant):
    clear_workspace_context()
    return AgentDefinition.no_workspace_objects.create(
        agent_name=f"agent-{uuid.uuid4().hex[:6]}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        # A NOT NULL column with no model default; True is what the create
        # endpoint stores when a request leaves it out.
        inbound=True,
        organization=tenant.organization,
        workspace=tenant.workspace,
    )


# --- eval groups and templates --------


class TestEvalGroupsAndTemplates:
    def test_run_test_create_refuses_foreign_eval_group_like_unknown(self, tenants):
        a, b = tenants
        agent = _agent(a)
        url = "/simulate/run-tests/create/"

        def payload(group_id):
            return {
                "name": f"rt-{uuid.uuid4().hex[:6]}",
                "agent_definition_id": str(agent.id),
                "scenario_ids": [str(_scenario(a).id)],
                "evaluations_config": [
                    {"template_id": str(uuid.uuid4()), "eval_group": str(group_id)}
                ],
            }

        foreign_group = _eval_group(b)
        unknown_id = uuid.uuid4()
        foreign = _post(a.client, url, payload(foreign_group.id))
        unknown = _post(a.client, url, payload(unknown_id))
        assert foreign.status_code == status.HTTP_400_BAD_REQUEST
        assert same_answer(foreign, foreign_group.id, unknown, unknown_id)
        assert not SimulateEvalConfig.no_workspace_objects.filter(
            eval_group=foreign_group
        ).exists()
        assert not RunTest.no_workspace_objects.filter(agent_definition=agent).exists()

    @pytest.mark.parametrize("other_scope", ["workspace", "organization"])
    def test_eval_group_create_uses_request_scope(self, tenants, other_scope):
        from types import SimpleNamespace
        from accounts.models.workspace import Workspace
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate

        a, b = tenants
        if other_scope == "workspace":
            clear_workspace_context()
            b = SimpleNamespace(organization=a.organization, user=a.user,
                workspace=Workspace.no_workspace_objects.create(
                    organization=a.organization, name="Other", created_by=a.user,
                    is_default=False, is_active=True,
                ))
        own, foreign = _eval_group(a), _eval_group(b)
        clear_workspace_context()
        template = EvalTemplate.no_workspace_objects.create(
            name=f"template-{uuid.uuid4()}", organization=a.organization,
            workspace=a.workspace, owner=OwnerChoices.USER.value,
            config={"required_keys": []},
        )
        agent, scenario = _agent(a), _scenario(a)
        def payload(group):
            return {"name": f"Run {uuid.uuid4()}", "agent_definition_id": str(agent.pk),
                    "scenario_ids": [str(scenario.pk)], "evaluations_config": [
                        {"template_id": str(template.pk), "eval_group": str(group.pk)}]}
        control = _post(a.client, "/simulate/run-tests/create/", payload(own))
        assert control.status_code in (200, 201), control.content
        assert SimulateEvalConfig.no_workspace_objects.filter(eval_group=own).exists()
        before = RunTest.no_workspace_objects.count()
        response = _post(a.client, "/simulate/run-tests/create/", payload(foreign))
        assert response.status_code == 400, response.content
        assert "Eval group not found" in str(response.data)
        assert not SimulateEvalConfig.no_workspace_objects.filter(eval_group=foreign).exists()
        assert RunTest.no_workspace_objects.count() == before

    def test_prompt_simulation_binds_no_foreign_template_or_group(self, tenants):
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate

        a, b = tenants
        clear_workspace_context()
        template = PromptTemplate.no_workspace_objects.create(
            name="ps",
            organization=a.organization,
            workspace=a.workspace,
            created_by=a.user,
        )
        version = PromptVersion.no_workspace_objects.create(
            original_template=template,
            template_version="v1",
            prompt_config_snapshot=[],
        )
        foreign_template = EvalTemplate.no_workspace_objects.create(
            name="foreign-sim-template",
            organization=b.organization,
            workspace=b.workspace,
            owner=OwnerChoices.USER.value,
            config={"required_keys": []},
        )
        url = f"/simulate/prompt-templates/{template.id}/simulations/"
        with_template = _post(
            a.client,
            url,
            {
                "name": "ps-run",
                "prompt_version_id": str(version.id),
                "scenario_ids": [str(_scenario(a).id)],
                "evaluations_config": [{"template_id": str(foreign_template.id)}],
            },
        )
        assert not SimulateEvalConfig.no_workspace_objects.filter(
            eval_template=foreign_template
        ).exists()
        assert with_template.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR
        with_group = _post(
            a.client,
            url,
            {
                "name": "ps-run-2",
                "prompt_version_id": str(version.id),
                "scenario_ids": [str(_scenario(a).id)],
                "evaluations_config": [
                    {
                        "template_id": str(foreign_template.id),
                        "eval_group": str(_eval_group(b).id),
                    }
                ],
            },
        )
        assert with_group.status_code == status.HTTP_400_BAD_REQUEST


# --- personas ----------------------------


class TestScenarioPersonas:
    @patch("simulate.views.scenarios.start_create_dataset_scenario_workflow_sync")
    @patch("tfc.ee_gating.check_ee_feature")
    def test_scenario_create_refuses_foreign_persona(self, _gate, workflow, tenants):
        a, b = tenants
        clear_workspace_context()
        foreign_persona = Persona.no_workspace_objects.create(
            name="foreign persona",
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=b.organization,
            workspace=b.workspace,
        )
        own_persona = Persona.no_workspace_objects.create(
            name="own persona", persona_type=Persona.PersonaType.WORKSPACE,
            organization=a.organization, workspace=a.workspace,
        )
        from model_hub.models.develop_dataset import Row

        dataset = _own_dataset(a)
        Row.no_workspace_objects.bulk_create([Row(dataset=dataset, order=i) for i in range(10)])
        payload = {
            "name": "Scenario", "kind": "dataset", "dataset_id": str(dataset.pk),
            "agent_definition_id": str(_agent(a).pk), "personas": [str(foreign_persona.pk)],
        }
        response = _post(a.client, "/simulate/scenarios/create/", payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "Persona not found or not accessible." in str(response.data)
        workflow.assert_not_called()
        payload["personas"] = [str(own_persona.pk)]
        control = _post(a.client, "/simulate/scenarios/create/", payload)
        assert control.status_code == status.HTTP_202_ACCEPTED, control.content
        workflow.assert_called_once()


# --- agent knowledge bases --------------------------------


class TestAgentKnowledgeBase:
    def test_agent_edit_refuses_foreign_kb_like_unknown(self, tenants):
        a, b = tenants
        agent = _agent(a)
        url = f"/simulate/agent-definitions/{agent.id}/edit/"
        foreign_kb = _kb(b)
        unknown_id = uuid.uuid4()
        clear_workspace_context()
        foreign = a.client.put(
            url, {"knowledge_base": str(foreign_kb.id)}, format="json"
        )
        clear_workspace_context()
        from django.db import transaction

        # Keep the unknown-id control observable on older implementations without
        # leaving a deferred FK violation for fixture teardown.
        with transaction.atomic():
            unknown = a.client.put(
                url, {"knowledge_base": str(unknown_id)}, format="json"
            )
            transaction.set_rollback(True)
        assert foreign.status_code == status.HTTP_400_BAD_REQUEST
        assert same_answer(foreign, foreign_kb.id, unknown, unknown_id)
        agent.refresh_from_db()
        assert agent.knowledge_base_id is None

    def test_agent_edit_own_kb_control(self, tenants):
        a, _ = tenants
        agent = _agent(a)
        kb = _kb(a)
        clear_workspace_context()
        response = a.client.put(
            f"/simulate/agent-definitions/{agent.id}/edit/",
            {"knowledge_base": str(kb.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        agent.refresh_from_db()
        assert agent.knowledge_base_id == kb.id


# --- chat batch -------------------------------------------


class TestChatBatch:
    def test_foreign_test_execution_answers_like_unknown(self, tenants):
        a, b = tenants
        clear_workspace_context()
        run_test = RunTest.no_workspace_objects.create(
            name="b run",
            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            organization=b.organization,
            workspace=b.workspace,
            agent_definition=_agent(b),
        )
        execution = TestExecution.no_workspace_objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_calls=0,
            completed_calls=0,
            failed_calls=0,
        )
        unknown_id = uuid.uuid4()
        url = "/simulate/test-executions/{}/chat/call-executions/batch/"
        foreign = _post(a.client, url.format(execution.id), {})
        unknown = _post(a.client, url.format(unknown_id), {})
        assert same_answer(foreign, execution.id, unknown, unknown_id)
        assert not CallExecution.no_workspace_objects.filter(
            test_execution=execution
        ).exists()


# --- agent playground -------------------


def _graph(tenant, name, template=False, status_=GraphVersionStatus.ACTIVE):
    clear_workspace_context()
    graph = Graph.no_workspace_objects.create(
        organization=None if template else tenant.organization,
        workspace=None if template else tenant.workspace,
        name=name,
        created_by=None if template else tenant.user,
        is_template=template,
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status=status_, tags=[]
    )
    return graph, version


class TestPlaygroundReferences:
    def test_version_create_refuses_a_foreign_subgraph_version(self, tenants):
        a, b = tenants
        own_graph, _ = _graph(a, "own graph")
        _, foreign_version = _graph(b, "b graph")
        response = _post(
            a.client,
            f"/agent-playground/graphs/{own_graph.id}/versions/",
            {
                "status": GraphVersionStatus.DRAFT,
                "nodes": [
                    {
                        "id": str(uuid.uuid4()),
                        "type": NodeType.SUBGRAPH,
                        "name": "sub",
                        "ref_graph_version_id": str(foreign_version.id),
                    }
                ],
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Node.no_workspace_objects.filter(
            ref_graph_version=foreign_version
        ).exists()

    def test_ref_graph_version_resolver_accepts_own_and_template(self, tenants):
        from django.core.exceptions import ValidationError

        from agent_playground.utils.version_content import create_node

        a, b = tenants
        _, own_version = _graph(a, "own ref")
        _, template_version = _graph(a, "tpl ref", template=True)
        _, foreign_version = _graph(b, "b ref")
        _, destination = _graph(a, "destination")
        def create_reference(version):
            return create_node(destination, {
                "id": uuid.uuid4(), "type": NodeType.SUBGRAPH, "name": f"Reference {uuid.uuid4()}",
                "ref_graph_version_id": str(version.pk),
            }, skip_validation=True)
        assert create_reference(own_version).ref_graph_version_id == own_version.pk
        assert create_reference(template_version).ref_graph_version_id == template_version.pk
        with pytest.raises(ValidationError):
            create_reference(foreign_version)

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_template_graph_metadata_write_is_not_persisted(self, tenants, method):
        a, _ = tenants
        template, _ = _graph(a, "shared template", template=True)
        clear_workspace_context()
        response = getattr(a.client, method)(
            f"/agent-playground/graphs/{template.pk}/?is_template=true",
            {"name": "Changed template"}, format="json",
        )
        template.refresh_from_db()
        # Assert storage first: even a late response-serialization or signal
        # error must not disguise a write that already reached the database.
        assert template.name == "shared template", (response.status_code, template.name)
        assert response.status_code in (403, 404), response.content

    def test_template_graph_writes_are_refused(self, tenants):
        a, _ = tenants
        template, version = _graph(a, "shared template", template=True)
        base = f"/agent-playground/graphs/{template.id}"
        clear_workspace_context()
        responses = [
            a.client.patch(f"{base}/?is_template=true", {"name": "x"}, format="json"),
            a.client.put(f"{base}/?is_template=true", {"name": "x"}, format="json"),
            a.client.delete(f"{base}/versions/{version.id}/?is_template=true"),
            a.client.post(f"{base}/versions/{version.id}/activate/?is_template=true"),
            a.client.delete(f"{base}/?is_template=true"),
            a.client.post(
                "/agent-playground/graphs/delete/?is_template=true",
                {"select_all": True},
                format="json",
            ),
        ]
        template.refresh_from_db()
        assert template.name == "shared template" and not template.deleted
        for response in responses:
            assert response.status_code in (
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ), response.content
        clear_workspace_context()
        listed = a.client.get("/agent-playground/graphs/?is_template=true")
        assert listed.status_code == status.HTTP_200_OK
