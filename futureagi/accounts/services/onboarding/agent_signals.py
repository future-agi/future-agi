from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from accounts.models import OnboardingActivationEvent
from accounts.services.onboarding.activation_events import has_event, latest_event

AGENT_SOURCE_PLAYGROUND = "agent_playground"
AGENT_SOURCE_SIMULATE = "simulate"


@dataclass(frozen=True)
class AgentOnboardingSignals:
    agent_count: int = 0
    sample_agent_count: int = 0
    agent_id: str | None = None
    agent_source: str | None = None
    agent_version_id: str | None = None
    has_agent_version: bool = False
    scenario_count: int = 0
    scenario_id: str | None = None
    run_count: int = 0
    run_source: str | None = None
    test_id: str | None = None
    execution_id: str | None = None
    call_execution_id: str | None = None
    graph_execution_id: str | None = None
    run_status: str | None = None
    run_completed_at: object | None = None
    run_failed: bool = False
    has_review: bool = False
    reviewed_at: object | None = None
    has_eval_coverage: bool = False
    eval_config_id: str | None = None
    is_sample_only: bool = False
    voice_feature_unavailable: bool = False
    permission_limited: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def has_agent(self):
        return self.agent_count > 0

    @property
    def has_scenario(self):
        return self.scenario_count > 0

    @property
    def has_run(self):
        return self.run_count > 0

    @property
    def has_multiple_scenarios(self):
        return self.scenario_count > 1

    @property
    def first_loop_completed(self):
        return (
            self.has_agent
            and self.has_run
            and self.has_review
            and self.has_eval_coverage
        )

    def to_activation_agent_state(self, stage):
        return {
            "agent_id": self.agent_id,
            "agent_source": self.agent_source,
            "agent_version_id": self.agent_version_id,
            "scenario_id": self.scenario_id,
            "test_id": self.test_id,
            "execution_id": self.execution_id,
            "call_execution_id": self.call_execution_id,
            "graph_execution_id": self.graph_execution_id,
            "run_status": self.run_status,
            "run_completed_at": self.run_completed_at,
            "stage": stage,
            "has_agent": self.has_agent,
            "has_agent_version": self.has_agent_version,
            "has_scenario": self.has_scenario,
            "has_run": self.has_run,
            "has_review": self.has_review,
            "has_eval_coverage": self.has_eval_coverage,
            "is_sample": self.is_sample_only,
            "sample_agent_count": self.sample_agent_count,
            "voice_feature_unavailable": self.voice_feature_unavailable,
            "permission_limited": self.permission_limited,
            "diagnostics": list(self.diagnostics),
        }


def _real_scenario_filter():
    return Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False)


def _latest_agent_event(
    *,
    organization,
    workspace,
    event_names,
    is_sample=False,
    execution_id=None,
    graph_execution_id=None,
    call_execution_id=None,
):
    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        event_name__in=event_names,
        product_path="agent",
        is_sample=is_sample,
    )
    matchers = Q()
    if execution_id:
        matchers |= Q(metadata__execution_id=str(execution_id))
    if graph_execution_id:
        matchers |= Q(metadata__graph_execution_id=str(graph_execution_id))
    if call_execution_id:
        matchers |= Q(metadata__call_execution_id=str(call_execution_id))
    if matchers:
        matched = queryset.filter(matchers).order_by("-occurred_at", "-created_at")
        event = matched.first()
        if event:
            return event
    return queryset.order_by("-occurred_at", "-created_at").first()


def _graph_evidence(*, organization, workspace):
    from agent_playground.models.choices import (
        GraphExecutionStatus,
        GraphVersionStatus,
    )
    from agent_playground.models.graph import Graph
    from agent_playground.models.graph_execution import GraphExecution
    from agent_playground.models.graph_version import GraphVersion

    graphs = Graph.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        is_template=False,
    ).order_by("-updated_at", "-created_at")
    graph_ids = list(graphs.values_list("id", flat=True)[:100])
    if not graph_ids:
        return {}

    terminal_execution = (
        GraphExecution.no_workspace_objects.filter(
            graph_version__graph_id__in=graph_ids,
            status__in=[
                GraphExecutionStatus.SUCCESS,
                GraphExecutionStatus.FAILED,
            ],
            completed_at__isnull=False,
        )
        .select_related("graph_version", "graph_version__graph")
        .order_by("-completed_at", "-updated_at", "-created_at")
        .first()
    )
    if terminal_execution:
        graph = terminal_execution.graph_version.graph
        version = terminal_execution.graph_version
    else:
        version = (
            GraphVersion.no_workspace_objects.filter(
                graph_id__in=graph_ids,
                status=GraphVersionStatus.ACTIVE,
            )
            .select_related("graph")
            .order_by("-updated_at", "-created_at")
            .first()
        )
        graph = version.graph if version else graphs.first()

    if not graph:
        return {}

    if not version:
        version = (
            GraphVersion.no_workspace_objects.filter(graph=graph)
            .order_by("-updated_at", "-created_at")
            .first()
        )

    return {
        "agent_count": len(graph_ids),
        "agent": graph,
        "version": version,
        "execution": terminal_execution,
        "completed_at": (
            terminal_execution.completed_at if terminal_execution else None
        ),
    }


def _simulation_evidence(*, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition, AgentTypeChoices
    from simulate.models.agent_version import AgentVersion
    from simulate.models.eval_config import SimulateEvalConfig
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    definitions = AgentDefinition.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
    ).order_by("-updated_at", "-created_at")
    definition_ids = list(definitions.values_list("id", flat=True)[:100])
    if not definition_ids:
        return {}

    terminal_execution = (
        TestExecution.no_workspace_objects.filter(
            run_test__source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            run_test__agent_definition_id__in=definition_ids,
            run_test__workspace=workspace,
        )
        .filter(
            Q(
                status__in=[
                    TestExecution.ExecutionStatus.COMPLETED,
                    TestExecution.ExecutionStatus.FAILED,
                ]
            )
            | Q(
                status=TestExecution.ExecutionStatus.CANCELLED,
                completed_calls__gt=0,
            )
        )
        .select_related("run_test", "agent_definition", "agent_version")
        .order_by("-completed_at", "-updated_at", "-created_at")
        .first()
    )

    if terminal_execution:
        definition = (
            terminal_execution.agent_definition
            or terminal_execution.run_test.agent_definition
        )
        version = (
            terminal_execution.agent_version
            or terminal_execution.run_test.agent_version
        )
        run_test = terminal_execution.run_test
    else:
        definition = (
            definitions.filter(agent_type=AgentTypeChoices.TEXT).first()
            or definitions.first()
        )
        version = (
            AgentVersion.no_workspace_objects.filter(agent_definition=definition)
            .order_by("-updated_at", "-created_at")
            .first()
            if definition
            else None
        )
        run_test = (
            RunTest.no_workspace_objects.filter(
                organization=organization,
                workspace=workspace,
                source_type=RunTest.SourceTypes.AGENT_DEFINITION,
                agent_definition=definition,
            )
            .order_by("-updated_at", "-created_at")
            .first()
            if definition
            else None
        )

    if not definition:
        return {}

    scenarios = Scenarios.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
    ).filter(_real_scenario_filter())
    if definition:
        scenarios = scenarios.filter(agent_definition=definition)
    scenario = scenarios.order_by("-updated_at", "-created_at").first()
    scenario_count = scenarios.count()

    if run_test and scenario_count == 0:
        run_scenarios = run_test.scenarios.filter(_real_scenario_filter())
        scenario = run_scenarios.order_by("-updated_at", "-created_at").first()
        scenario_count = run_scenarios.count()

    call_execution = None
    if terminal_execution:
        call_execution = (
            terminal_execution.calls.filter(status=CallExecution.CallStatus.COMPLETED)
            .order_by("-completed_at", "-updated_at", "-created_at")
            .first()
            or terminal_execution.calls.order_by("-updated_at", "-created_at").first()
        )

    eval_config = (
        SimulateEvalConfig.no_workspace_objects.filter(run_test=run_test)
        .order_by("-updated_at", "-created_at")
        .first()
        if run_test
        else None
    )

    return {
        "agent_count": len(definition_ids),
        "agent": definition,
        "version": version,
        "scenario": scenario,
        "scenario_count": scenario_count,
        "run_test": run_test,
        "execution": terminal_execution,
        "call_execution": call_execution,
        "eval_config": eval_config,
        "completed_at": (
            terminal_execution.completed_at if terminal_execution else None
        ),
    }


def _choose_source(graph_evidence, simulation_evidence):
    graph_run_at = graph_evidence.get("completed_at")
    simulation_run_at = simulation_evidence.get("completed_at")
    if graph_run_at and simulation_run_at:
        return (
            AGENT_SOURCE_PLAYGROUND
            if graph_run_at >= simulation_run_at
            else AGENT_SOURCE_SIMULATE
        )
    if graph_run_at:
        return AGENT_SOURCE_PLAYGROUND
    if simulation_run_at:
        return AGENT_SOURCE_SIMULATE

    graph_agent = graph_evidence.get("agent")
    simulation_agent = simulation_evidence.get("agent")
    if graph_agent and simulation_agent:
        return (
            AGENT_SOURCE_PLAYGROUND
            if graph_agent.updated_at >= simulation_agent.updated_at
            else AGENT_SOURCE_SIMULATE
        )
    if graph_agent:
        return AGENT_SOURCE_PLAYGROUND
    if simulation_agent:
        return AGENT_SOURCE_SIMULATE
    return None


def collect_agent_onboarding_signals(*, user, organization, workspace):
    if not organization or not workspace:
        return AgentOnboardingSignals()

    graph = _graph_evidence(organization=organization, workspace=workspace)
    simulation = _simulation_evidence(organization=organization, workspace=workspace)
    source = _choose_source(graph, simulation)

    sample_agent_count = (
        1
        if has_event(
            organization=organization,
            workspace=workspace,
            event_name="agent_created",
            is_sample=True,
        )
        else 0
    )

    if not source:
        return AgentOnboardingSignals(
            sample_agent_count=sample_agent_count,
            is_sample_only=sample_agent_count > 0,
        )

    evidence = graph if source == AGENT_SOURCE_PLAYGROUND else simulation
    agent = evidence.get("agent")
    version = evidence.get("version")
    execution = evidence.get("execution")
    agent_count = graph.get("agent_count", 0) + simulation.get("agent_count", 0)
    scenario = simulation.get("scenario")
    run_test = simulation.get("run_test")
    call_execution = simulation.get("call_execution")
    eval_config = simulation.get("eval_config")

    graph_execution_id = None
    execution_id = None
    if source == AGENT_SOURCE_PLAYGROUND and execution:
        graph_execution_id = str(execution.id)
    elif source == AGENT_SOURCE_SIMULATE and execution:
        execution_id = str(execution.id)

    review_event = _latest_agent_event(
        organization=organization,
        workspace=workspace,
        event_names=["agent_trace_reviewed"],
        is_sample=False,
        execution_id=execution_id,
        graph_execution_id=graph_execution_id,
        call_execution_id=str(call_execution.id) if call_execution else None,
    )
    eval_event = _latest_agent_event(
        organization=organization,
        workspace=workspace,
        event_names=["agent_scenario_saved_as_eval", "agent_eval_created"],
        is_sample=False,
        execution_id=execution_id,
        graph_execution_id=graph_execution_id,
        call_execution_id=str(call_execution.id) if call_execution else None,
    )
    run_event = latest_event(
        organization=organization,
        workspace=workspace,
        event_names=["agent_prototype_run_completed"],
        product_path="agent",
        is_sample=False,
    )

    has_run = bool(execution) or bool(run_event)
    run_status = getattr(execution, "status", None) if execution else None
    run_failed = run_status in {"failed", "cancelled"}
    run_completed_at = getattr(execution, "completed_at", None) or (
        run_event.occurred_at if run_event else None
    )
    has_eval_coverage = bool(eval_config or eval_event)

    diagnostics = []
    if source == AGENT_SOURCE_PLAYGROUND and not run_test and review_event:
        diagnostics.append("agent_eval_route_needs_simulate_test")
    if sample_agent_count and agent_count:
        diagnostics.append("sample_agent_ignored_for_real_activation")

    return AgentOnboardingSignals(
        agent_count=agent_count,
        sample_agent_count=sample_agent_count,
        agent_id=str(agent.id) if agent else None,
        agent_source=source,
        agent_version_id=str(version.id) if version else None,
        has_agent_version=bool(version),
        scenario_count=simulation.get("scenario_count", 0),
        scenario_id=str(scenario.id) if scenario else None,
        run_count=1 if has_run else 0,
        run_source=source if has_run else None,
        test_id=str(run_test.id) if run_test else None,
        execution_id=execution_id,
        call_execution_id=str(call_execution.id) if call_execution else None,
        graph_execution_id=graph_execution_id,
        run_status=run_status,
        run_completed_at=run_completed_at,
        run_failed=run_failed,
        has_review=bool(review_event),
        reviewed_at=review_event.occurred_at if review_event else None,
        has_eval_coverage=has_eval_coverage,
        eval_config_id=str(eval_config.id) if eval_config else None,
        is_sample_only=False,
        voice_feature_unavailable=False,
        permission_limited=False,
        diagnostics=tuple(diagnostics),
    )
