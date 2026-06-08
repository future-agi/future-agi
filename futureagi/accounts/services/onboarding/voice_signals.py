from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from accounts.models import OnboardingActivationEvent

VOICE_SOURCE_SIMULATE = "simulate"


@dataclass(frozen=True)
class VoiceOnboardingSignals:
    agent_count: int = 0
    sample_call_count: int = 0
    agent_id: str | None = None
    agent_name: str | None = None
    agent_provider: str | None = None
    agent_version_id: str | None = None
    scenario_count: int = 0
    scenario_id: str | None = None
    run_test_count: int = 0
    run_test_id: str | None = None
    test_execution_id: str | None = None
    call_count: int = 0
    call_execution_id: str | None = None
    call_status: str | None = None
    call_completed_at: object | None = None
    call_duration_seconds: int | None = None
    call_response_time_ms: int | None = None
    call_interruption_count: int | None = None
    transcript_available: bool = False
    recording_available: bool = False
    review_event_id: str | None = None
    reviewed_at: object | None = None
    success_criteria_event_id: str | None = None
    success_criteria_at: object | None = None
    eval_config_id: str | None = None
    is_sample_only: bool = False
    permission_limited: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def has_agent(self):
        return self.agent_count > 0 and not self.is_sample_only

    @property
    def has_scenario(self):
        return self.scenario_count > 0

    @property
    def has_test(self):
        return self.run_test_count > 0

    @property
    def has_call(self):
        return self.call_count > 0

    @property
    def has_completed_call(self):
        return self.has_call and self.call_status == "completed"

    @property
    def call_failed(self):
        return self.call_status in {"failed", "cancelled"}

    @property
    def has_review(self):
        return bool(self.review_event_id)

    @property
    def has_success_criteria(self):
        return bool(self.eval_config_id or self.success_criteria_event_id)

    @property
    def first_loop_completed(self):
        return (
            self.has_agent
            and self.has_completed_call
            and self.has_review
            and self.has_success_criteria
        )

    def to_activation_voice_state(self, stage):
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_provider": self.agent_provider,
            "agent_version_id": self.agent_version_id,
            "scenario_id": self.scenario_id,
            "run_test_id": self.run_test_id,
            "test_execution_id": self.test_execution_id,
            "call_execution_id": self.call_execution_id,
            "call_status": self.call_status,
            "call_completed_at": self.call_completed_at,
            "call_duration_seconds": self.call_duration_seconds,
            "call_response_time_ms": self.call_response_time_ms,
            "call_interruption_count": self.call_interruption_count,
            "transcript_available": self.transcript_available,
            "recording_available": self.recording_available,
            "reviewed_at": self.reviewed_at,
            "success_criteria_at": self.success_criteria_at,
            "eval_config_id": self.eval_config_id,
            "stage": stage,
            "has_agent": self.has_agent,
            "has_scenario": self.has_scenario,
            "has_test": self.has_test,
            "has_call": self.has_call,
            "has_completed_call": self.has_completed_call,
            "call_failed": self.call_failed,
            "has_review": self.has_review,
            "has_success_criteria": self.has_success_criteria,
            "is_sample": self.is_sample_only,
            "sample_call_count": self.sample_call_count,
            "permission_limited": self.permission_limited,
            "diagnostics": list(self.diagnostics),
        }


def _real_scenario_filter():
    return Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False)


def _real_call_filter():
    return Q(call_metadata__is_sample__isnull=True) | Q(call_metadata__is_sample=False)


def _real_execution_filter():
    return Q(test_execution__execution_metadata__is_sample__isnull=True) | Q(
        test_execution__execution_metadata__is_sample=False
    )


def _latest_voice_event(
    *,
    organization,
    workspace,
    event_names,
    is_sample=False,
    agent_id=None,
    run_test_id=None,
    test_execution_id=None,
    call_execution_id=None,
    scenario_id=None,
):
    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        event_name__in=event_names,
        product_path="voice",
        is_sample=is_sample,
    )
    matchers = Q()
    if agent_id:
        matchers |= Q(metadata__agent_id=str(agent_id))
        matchers |= Q(metadata__voice_agent_id=str(agent_id))
    if run_test_id:
        matchers |= Q(metadata__run_test_id=str(run_test_id))
        matchers |= Q(metadata__test_id=str(run_test_id))
    if test_execution_id:
        matchers |= Q(metadata__test_execution_id=str(test_execution_id))
        matchers |= Q(metadata__execution_id=str(test_execution_id))
    if call_execution_id:
        matchers |= Q(metadata__call_execution_id=str(call_execution_id))
        matchers |= Q(metadata__voice_call_id=str(call_execution_id))
    if scenario_id:
        matchers |= Q(metadata__scenario_id=str(scenario_id))

    if matchers:
        event = (
            queryset.filter(matchers).order_by("-occurred_at", "-created_at").first()
        )
        if event:
            return event
    return queryset.order_by("-occurred_at", "-created_at").first()


def _voice_evidence(*, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition, AgentTypeChoices
    from simulate.models.agent_version import AgentVersion
    from simulate.models.eval_config import SimulateEvalConfig
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    definitions = AgentDefinition.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        agent_type=AgentTypeChoices.VOICE,
    ).order_by("-updated_at", "-created_at")
    definition_ids = list(definitions.values_list("id", flat=True)[:100])
    if not definition_ids:
        return {}

    calls = (
        CallExecution.no_workspace_objects.filter(
            test_execution__run_test__source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            test_execution__run_test__agent_definition_id__in=definition_ids,
            test_execution__run_test__workspace=workspace,
            simulation_call_type=AgentTypeChoices.VOICE,
            status__in=[
                CallExecution.CallStatus.COMPLETED,
                CallExecution.CallStatus.FAILED,
                CallExecution.CallStatus.CANCELLED,
            ],
        )
        .filter(_real_call_filter())
        .filter(_real_execution_filter())
        .select_related(
            "scenario",
            "test_execution",
            "test_execution__run_test",
            "test_execution__agent_definition",
            "test_execution__agent_version",
            "test_execution__run_test__agent_definition",
            "test_execution__run_test__agent_version",
        )
        .order_by("-completed_at", "-updated_at", "-created_at")
    )
    call = calls.first()

    if call:
        execution = call.test_execution
        run_test = execution.run_test
        agent = execution.agent_definition or run_test.agent_definition
        version = execution.agent_version or run_test.agent_version
        scenario = call.scenario
    else:
        agent = definitions.first()
        version = (
            AgentVersion.no_workspace_objects.filter(agent_definition=agent)
            .order_by("-updated_at", "-created_at")
            .first()
            if agent
            else None
        )
        run_test = (
            RunTest.no_workspace_objects.filter(
                organization=organization,
                workspace=workspace,
                source_type=RunTest.SourceTypes.AGENT_DEFINITION,
                agent_definition=agent,
            )
            .order_by("-updated_at", "-created_at")
            .first()
            if agent
            else None
        )
        execution = (
            TestExecution.no_workspace_objects.filter(run_test=run_test)
            .order_by("-updated_at", "-created_at")
            .first()
            if run_test
            else None
        )
        scenario = None

    scenarios = Scenarios.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
        agent_definition=agent,
    ).filter(_real_scenario_filter())
    if not scenario:
        scenario = scenarios.order_by("-updated_at", "-created_at").first()
    scenario_count = scenarios.count()
    if run_test and scenario_count == 0:
        run_scenarios = run_test.scenarios.filter(_real_scenario_filter())
        scenario = run_scenarios.order_by("-updated_at", "-created_at").first()
        scenario_count = run_scenarios.count()

    run_tests = RunTest.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
        agent_definition_id__in=definition_ids,
    )
    eval_config = (
        SimulateEvalConfig.no_workspace_objects.filter(run_test=run_test)
        .order_by("-updated_at", "-created_at")
        .first()
        if run_test
        else None
    )
    sample_call_count = (
        CallExecution.no_workspace_objects.filter(
            test_execution__run_test__source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            test_execution__run_test__agent_definition_id__in=definition_ids,
            test_execution__run_test__workspace=workspace,
            simulation_call_type=AgentTypeChoices.VOICE,
        )
        .filter(
            Q(call_metadata__is_sample=True)
            | Q(test_execution__execution_metadata__is_sample=True)
            | Q(scenario__metadata__is_sample=True)
        )
        .count()
    )

    return {
        "agent_count": len(definition_ids),
        "agent": agent,
        "version": version,
        "scenario": scenario,
        "scenario_count": scenario_count,
        "run_test": run_test,
        "run_test_count": run_tests.count(),
        "execution": execution,
        "call": call,
        "call_count": calls.count(),
        "eval_config": eval_config,
        "sample_call_count": sample_call_count,
    }


def collect_voice_onboarding_signals(*, user, organization, workspace):
    if not organization or not workspace:
        return VoiceOnboardingSignals()

    evidence = _voice_evidence(organization=organization, workspace=workspace)
    sample_event = _latest_voice_event(
        organization=organization,
        workspace=workspace,
        event_names=[
            "voice_agent_created",
            "voice_test_call_completed",
            "voice_call_reviewed",
        ],
        is_sample=True,
    )
    if not evidence:
        return VoiceOnboardingSignals(
            sample_call_count=1 if sample_event else 0,
            is_sample_only=bool(sample_event),
        )

    agent = evidence.get("agent")
    version = evidence.get("version")
    scenario = evidence.get("scenario")
    run_test = evidence.get("run_test")
    execution = evidence.get("execution")
    call = evidence.get("call")
    eval_config = evidence.get("eval_config")

    review_event = _latest_voice_event(
        organization=organization,
        workspace=workspace,
        event_names=["voice_call_reviewed"],
        is_sample=False,
        agent_id=getattr(agent, "id", None),
        run_test_id=getattr(run_test, "id", None),
        test_execution_id=getattr(execution, "id", None),
        call_execution_id=getattr(call, "id", None),
        scenario_id=getattr(scenario, "id", None),
    )
    criteria_event = _latest_voice_event(
        organization=organization,
        workspace=workspace,
        event_names=["voice_success_criteria_added"],
        is_sample=False,
        agent_id=getattr(agent, "id", None),
        run_test_id=getattr(run_test, "id", None),
        test_execution_id=getattr(execution, "id", None),
        call_execution_id=getattr(call, "id", None),
        scenario_id=getattr(scenario, "id", None),
    )

    diagnostics = []
    sample_call_count = evidence.get("sample_call_count", 0) + (
        1 if sample_event else 0
    )
    if sample_call_count and evidence.get("call_count", 0):
        diagnostics.append("sample_voice_call_ignored_for_real_activation")
    if call and call.status in {"failed", "cancelled"}:
        diagnostics.append("voice_call_needs_successful_rerun")
    if review_event and not (eval_config or criteria_event):
        diagnostics.append("voice_review_needs_success_criteria")

    interruption_count = None
    if call:
        interruption_count = (call.user_interruption_count or 0) + (
            call.ai_interruption_count or 0
        )

    return VoiceOnboardingSignals(
        agent_count=evidence.get("agent_count", 0),
        sample_call_count=sample_call_count,
        agent_id=str(agent.id) if agent else None,
        agent_name=getattr(agent, "agent_name", None) if agent else None,
        agent_provider=getattr(agent, "provider", None) if agent else None,
        agent_version_id=str(version.id) if version else None,
        scenario_count=evidence.get("scenario_count", 0),
        scenario_id=str(scenario.id) if scenario else None,
        run_test_count=evidence.get("run_test_count", 0),
        run_test_id=str(run_test.id) if run_test else None,
        test_execution_id=str(execution.id) if execution else None,
        call_count=evidence.get("call_count", 0),
        call_execution_id=str(call.id) if call else None,
        call_status=getattr(call, "status", None) if call else None,
        call_completed_at=getattr(call, "completed_at", None) if call else None,
        call_duration_seconds=getattr(call, "duration_seconds", None) if call else None,
        call_response_time_ms=getattr(call, "response_time_ms", None) if call else None,
        call_interruption_count=interruption_count,
        transcript_available=bool(
            getattr(call, "transcript_available", False) if call else False
        ),
        recording_available=bool(
            getattr(call, "recording_available", False) if call else False
        ),
        review_event_id=str(review_event.id) if review_event else None,
        reviewed_at=review_event.occurred_at if review_event else None,
        success_criteria_event_id=str(criteria_event.id) if criteria_event else None,
        success_criteria_at=criteria_event.occurred_at if criteria_event else None,
        eval_config_id=str(eval_config.id) if eval_config else None,
        is_sample_only=False,
        diagnostics=tuple(diagnostics),
    )
