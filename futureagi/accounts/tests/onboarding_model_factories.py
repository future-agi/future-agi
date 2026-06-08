import uuid
from decimal import Decimal

from django.utils import timezone

from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
    NodeType,
    PortMode,
)
from agent_playground.models.graph import Graph
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_template import NodeTemplate
from agentcc.models import AgentccAPIKey, AgentccOrgConfig, AgentccRequestLog
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.routing_policy import AgentccRoutingPolicy
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.run_prompt import (
    PromptEvalConfig,
    PromptTemplate,
    PromptVersion,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.trace import Trace


def create_observe_project(
    *, organization, workspace, user=None, name=None, metadata=None, source="prototype"
):
    return Project.no_workspace_objects.create(
        name=name or f"Observe {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata=metadata,
        source=source,
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


def create_eval_dataset(
    *,
    organization,
    workspace,
    user=None,
    name=None,
    source=DatasetSourceChoices.BUILD.value,
):
    return Dataset.no_workspace_objects.create(
        name=name or f"Eval dataset {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        source=source,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        column_order=[],
        column_config={},
        dataset_config={},
    )


def create_user_eval_metric(
    *,
    organization,
    workspace,
    dataset,
    user=None,
    name=None,
):
    eval_template = EvalTemplate.no_workspace_objects.create(
        name=name or f"eval-quality-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    return UserEvalMetric.no_workspace_objects.create(
        name=name or f"eval-quality-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        template=eval_template,
        dataset=dataset,
        config={"mapping": {}, "config": {}},
        show_in_sidebar=True,
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


def create_agent_graph_node(*, graph_version, name=None):
    template = NodeTemplate.no_workspace_objects.create(
        name=f"onboarding_llm_prompt_{uuid.uuid4().hex[:8]}",
        display_name="LLM Prompt",
        description="Prompt step for onboarding tests.",
        categories=["llm"],
        input_definition=[],
        output_definition=[],
        input_mode=PortMode.DYNAMIC,
        output_mode=PortMode.DYNAMIC,
        config_schema={},
    )
    return Node.no_workspace_objects.create(
        graph_version=graph_version,
        node_template=template,
        type=NodeType.ATOMIC,
        name=name or f"Prompt step {uuid.uuid4().hex[:8]}",
        config={},
        position={"x": 100, "y": 100},
    )


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


def create_call_execution(
    *,
    test_execution,
    scenario,
    status="completed",
    simulation_call_type=None,
    metadata=None,
    transcript_available=True,
    recording_available=False,
    response_time_ms=None,
    user_interruption_count=None,
    ai_interruption_count=None,
):
    from simulate.models.agent_definition import AgentTypeChoices
    from simulate.models.test_execution import CallExecution

    return CallExecution.no_workspace_objects.create(
        test_execution=test_execution,
        simulation_call_type=simulation_call_type or AgentTypeChoices.TEXT,
        scenario=scenario,
        status=status,
        started_at=test_execution.started_at,
        completed_at=test_execution.completed_at,
        duration_seconds=1,
        call_summary="Agent answered the scenario.",
        call_metadata=metadata or {},
        transcript_available=transcript_available,
        recording_available=recording_available,
        response_time_ms=response_time_ms,
        user_interruption_count=user_interruption_count,
        ai_interruption_count=ai_interruption_count,
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


def create_gateway_provider(
    *,
    organization,
    workspace=None,
    provider_name=None,
    models_list=None,
    is_active=True,
):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        provider_name=provider_name or f"provider-{uuid.uuid4().hex[:8]}",
        display_name="Test provider",
        encrypted_credentials=b"encrypted",
        models_list=models_list if models_list is not None else ["gpt-4o-mini"],
        is_active=is_active,
    )


def create_gateway_key(
    *,
    organization,
    workspace=None,
    user=None,
    gateway_key_id=None,
    key_prefix="fagi_",
    status=AgentccAPIKey.ACTIVE,
):
    gateway_key_id = gateway_key_id or f"gw_key_{uuid.uuid4().hex}"
    return AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        gateway_key_id=gateway_key_id,
        key_prefix=key_prefix,
        key_hash=uuid.uuid4().hex,
        name="Test gateway key",
        status=status,
    )


def create_gateway_request_log(
    *,
    organization,
    workspace=None,
    gateway_key=None,
    request_id=None,
    status_code=200,
    is_error=False,
    started_at=None,
    metadata=None,
    fallback_used=False,
    guardrail_triggered=False,
):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        request_id=request_id or f"req_{uuid.uuid4().hex}",
        model="gpt-4o-mini",
        provider="openai",
        resolved_model="gpt-4o-mini",
        latency_ms=512,
        started_at=started_at or timezone.now(),
        input_tokens=12,
        output_tokens=18,
        total_tokens=30,
        cost=Decimal("0.002000"),
        status_code=status_code,
        is_error=is_error,
        error_message="Provider error" if is_error else "",
        cache_hit=False,
        fallback_used=fallback_used,
        guardrail_triggered=guardrail_triggered,
        api_key_id=gateway_key.gateway_key_id if gateway_key else "",
        session_id=f"session-{uuid.uuid4().hex[:8]}",
        routing_strategy="fallback" if fallback_used else "primary",
        metadata=metadata or {},
    )


def create_gateway_guardrail_policy(*, organization, name=None):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name=name or f"Guardrail {uuid.uuid4().hex[:8]}",
        checks=[{"name": "pii", "action": "block"}],
        is_active=True,
    )


def create_gateway_routing_policy(*, organization, user=None, name=None):
    return AgentccRoutingPolicy.no_workspace_objects.create(
        organization=organization,
        name=name or f"Routing {uuid.uuid4().hex[:8]}",
        config={"fallbacks": [{"provider": "openai"}]},
        is_active=True,
        created_by=user,
    )


def create_gateway_org_config(*, organization, workspace=None, user=None, **config):
    AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization,
        is_active=True,
    ).update(is_active=False)
    latest_version = (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
        or 0
    )
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        version=latest_version + 1,
        is_active=True,
        created_by=user,
        **config,
    )
