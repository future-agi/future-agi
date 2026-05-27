import uuid

from django.utils import timezone

from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
)
from agent_playground.models.graph import Graph
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.graph_version import GraphVersion
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import (
    PromptEvalConfig,
    PromptTemplate,
    PromptVersion,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.trace import Trace


def create_observe_project(
    *, organization, workspace, user=None, name=None, metadata=None
):
    return Project.no_workspace_objects.create(
        name=name or f"Observe {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata=metadata,
    )


def create_trace(*, project, name=None, with_payload=True, metadata=None, error=None):
    payload = {"value": "private"} if with_payload else None
    return Trace.no_workspace_objects.create(
        project=project,
        name=name or f"Trace {uuid.uuid4().hex[:8]}",
        metadata=metadata,
        input=payload,
        output=payload,
        error=error,
    )


def create_custom_eval(*, organization, workspace, project, name=None):
    template = EvalTemplate.no_workspace_objects.create(
        name=name or f"quality-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    return CustomEvalConfig.no_workspace_objects.create(
        name=name or f"quality-{uuid.uuid4().hex[:8]}",
        eval_template=template,
        project=project,
    )


def create_prompt_template(
    *, organization, workspace, user=None, name=None, is_sample=False
):
    template = PromptTemplate.no_workspace_objects.create(
        name=name or f"Prompt {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        created_by=user,
        is_sample=is_sample,
    )
    if user:
        template.collaborators.add(user)
    return template


def create_prompt_version(
    *,
    template,
    version="v1",
    is_draft=True,
    is_default=False,
    commit_message="",
    output=None,
):
    return PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version=version,
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "Say hello"}],
            "configuration": {"model": "gpt-4o-mini"},
        },
        variable_names={},
        metadata={},
        output=[] if output is None else output,
        is_draft=is_draft,
        is_default=is_default,
        commit_message=commit_message,
    )


def create_prompt_eval_config(*, organization, workspace, template, name=None):
    eval_template = EvalTemplate.no_workspace_objects.create(
        name=name or f"prompt-quality-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    return PromptEvalConfig.no_workspace_objects.create(
        name=name or f"prompt-quality-{uuid.uuid4().hex[:8]}",
        eval_template=eval_template,
        prompt_template=template,
    )


def create_agent_graph(*, organization, workspace, user, name=None):
    graph = Graph.no_workspace_objects.create(
        name=name or f"Agent graph {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        created_by=user,
        is_template=False,
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=1,
        status=GraphVersionStatus.ACTIVE,
    )
    return graph, version


def create_graph_execution(
    *,
    graph_version,
    status=GraphExecutionStatus.SUCCESS,
    completed_at=None,
):
    completed_at = completed_at or timezone.now()
    return GraphExecution.no_workspace_objects.create(
        graph_version=graph_version,
        status=status,
        started_at=completed_at,
        completed_at=completed_at,
        input_payload={"scenario": "hello"},
        output_payload={"result": "ok"},
    )


def create_agent_definition(
    *,
    organization,
    workspace,
    name=None,
    agent_type="text",
):
    from simulate.models.agent_definition import AgentDefinition

    return AgentDefinition.no_workspace_objects.create(
        agent_name=name or f"Agent definition {uuid.uuid4().hex[:8]}",
        agent_type=agent_type,
        inbound=False,
        description="Test agent",
        organization=organization,
        workspace=workspace,
        provider="test",
        model="test-model",
    )


def create_agent_version(*, agent_definition, status="active"):
    from simulate.models.agent_version import AgentVersion

    return AgentVersion.no_workspace_objects.create(
        agent_definition=agent_definition,
        organization=agent_definition.organization,
        workspace=agent_definition.workspace,
        version_number=1,
        status=status,
        description="Initial version",
        commit_message="Initial version",
        configuration_snapshot={"agent_name": agent_definition.agent_name},
    )


def create_agent_scenario(
    *,
    organization,
    workspace,
    agent_definition,
    name=None,
    metadata=None,
):
    from simulate.models.scenarios import Scenarios

    return Scenarios.no_workspace_objects.create(
        name=name or f"Scenario {uuid.uuid4().hex[:8]}",
        description="Agent scenario",
        source="Customer asks for help",
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
        metadata=metadata or {},
    )


def create_run_test(
    *, organization, workspace, agent_definition, agent_version, scenario
):
    from simulate.models.run_test import RunTest

    run_test = RunTest.no_workspace_objects.create(
        name=f"Agent test {uuid.uuid4().hex[:8]}",
        description="Agent onboarding test",
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        agent_version=agent_version,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )
    run_test.scenarios.add(scenario)
    return run_test


def create_test_execution(
    *,
    run_test,
    status="completed",
    completed_at=None,
):
    from simulate.models.test_execution import TestExecution

    completed_at = completed_at or timezone.now()
    return TestExecution.no_workspace_objects.create(
        run_test=run_test,
        status=status,
        started_at=completed_at,
        completed_at=completed_at,
        total_scenarios=run_test.scenarios.count(),
        scenario_ids=[str(scenario.id) for scenario in run_test.scenarios.all()],
        total_calls=1,
        completed_calls=1 if status == "completed" else 0,
        failed_calls=1 if status == "failed" else 0,
        agent_definition=run_test.agent_definition,
        agent_version=run_test.agent_version,
    )


def create_call_execution(*, test_execution, scenario, status="completed"):
    from simulate.models.agent_definition import AgentTypeChoices
    from simulate.models.test_execution import CallExecution

    return CallExecution.no_workspace_objects.create(
        test_execution=test_execution,
        simulation_call_type=AgentTypeChoices.TEXT,
        scenario=scenario,
        status=status,
        started_at=test_execution.started_at,
        completed_at=test_execution.completed_at,
        duration_seconds=1,
        call_summary="Agent answered the scenario.",
        transcript_available=True,
    )


def create_simulate_eval_config(*, run_test, organization, workspace, name=None):
    from simulate.models.eval_config import SimulateEvalConfig

    eval_template = EvalTemplate.no_workspace_objects.create(
        name=name or f"agent-quality-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    return SimulateEvalConfig.no_workspace_objects.create(
        name=name or f"agent-quality-{uuid.uuid4().hex[:8]}",
        eval_template=eval_template,
        run_test=run_test,
    )
