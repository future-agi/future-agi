from dataclasses import dataclass, field

from django.db.models import Q

from accounts.services.onboarding.activation_events import (
    first_quality_loop_completed,
    has_event,
    latest_event,
)
from accounts.services.onboarding.agent_signals import (
    AgentOnboardingSignals,
    collect_agent_onboarding_signals,
)
from accounts.services.onboarding.eval_signals import (
    EvalOnboardingSignals,
    collect_eval_onboarding_signals,
)
from accounts.services.onboarding.gateway_signals import (
    GatewayOnboardingSignals,
    collect_gateway_onboarding_signals,
)
from accounts.services.onboarding.prompt_signals import (
    PromptOnboardingSignals,
    collect_prompt_onboarding_signals,
)
from accounts.services.onboarding.voice_signals import (
    VoiceOnboardingSignals,
    collect_voice_onboarding_signals,
)

NON_REAL_OBSERVE_PROJECT_SOURCES = ("sample", "demo")


@dataclass(frozen=True)
class OnboardingSignals:
    first_checks: dict
    provider_keys: int = 0
    datasets: int = 0
    evals: int = 0
    eval_runs: int = 0
    eval_source_count: int = 0
    eval_source_type: str | None = None
    eval_source_id: str | None = None
    eval_source_name: str | None = None
    eval_scorer_count: int = 0
    eval_scorer_id: str | None = None
    eval_scorer_template_id: str | None = None
    eval_scorer_name: str | None = None
    eval_group_count: int = 0
    eval_group_id: str | None = None
    eval_run_count: int = 0
    eval_run_id: str | None = None
    eval_run_status: str | None = None
    eval_run_completed_at: object | None = None
    eval_failure_count: int = 0
    eval_has_source: bool = False
    eval_has_scorer: bool = False
    eval_has_completed_run: bool = False
    eval_has_failures: bool = False
    eval_has_review: bool = False
    eval_has_failure_action: bool = False
    eval_first_loop_completed: bool = False
    eval_is_sample_only: bool = False
    eval_sample_source_count: int = 0
    eval_permission_limited: bool = False
    eval_signals: EvalOnboardingSignals = field(default_factory=EvalOnboardingSignals)
    prompt_templates: int = 0
    prompt_versions: int = 0
    prompt_comparisons: int = 0
    first_prompt_id: str | None = None
    latest_prompt_id: str | None = None
    latest_prompt_name: str | None = None
    prompt_sample_templates: int = 0
    prompt_run_exists: bool = False
    prompt_committed_version_exists: bool = False
    prompt_comparable_versions_exist: bool = False
    prompt_comparison_completed: bool = False
    prompt_next_loop_action_exists: bool = False
    prompt_first_loop_completed: bool = False
    prompt_signals: PromptOnboardingSignals = field(
        default_factory=PromptOnboardingSignals
    )
    agents: int = 0
    agent_prototype_runs: int = 0
    agent_id: str | None = None
    agent_source: str | None = None
    agent_version_id: str | None = None
    agent_scenario_id: str | None = None
    agent_test_id: str | None = None
    agent_execution_id: str | None = None
    agent_call_execution_id: str | None = None
    agent_graph_execution_id: str | None = None
    agent_run_status: str | None = None
    agent_sample_count: int = 0
    agent_has_agent: bool = False
    agent_has_agent_version: bool = False
    agent_has_step: bool = False
    agent_has_scenario: bool = False
    agent_has_run: bool = False
    agent_run_failed: bool = False
    agent_has_review: bool = False
    agent_has_eval_coverage: bool = False
    agent_multiple_scenarios: bool = False
    agent_first_loop_completed: bool = False
    agent_voice_feature_unavailable: bool = False
    agent_permission_limited: bool = False
    agent_signals: AgentOnboardingSignals = field(
        default_factory=AgentOnboardingSignals
    )
    observe_projects: int = 0
    traces: int = 0
    trace_reviews: int = 0
    gateway_keys: int = 0
    gateway_requests: int = 0
    gateway_policies: int = 0
    gateway_available: bool = False
    gateway_id: str | None = None
    gateway_status: str | None = None
    gateway_public_url: str | None = None
    gateway_provider_count: int = 0
    gateway_provider_credential_id: str | None = None
    gateway_provider_name: str | None = None
    gateway_provider_health_status: str | None = None
    gateway_provider_model_count: int = 0
    gateway_has_provider: bool = False
    gateway_has_key: bool = False
    gateway_key_id: str | None = None
    gateway_key_prefix: str | None = None
    gateway_key_status: str | None = None
    gateway_has_request: bool = False
    gateway_request_log_id: str | None = None
    gateway_request_id: str | None = None
    gateway_request_status_code: int | None = None
    gateway_request_is_error: bool = False
    gateway_request_error_message: str | None = None
    gateway_request_provider: str | None = None
    gateway_request_model: str | None = None
    gateway_request_resolved_model: str | None = None
    gateway_request_latency_ms: int | None = None
    gateway_request_cost: str | None = None
    gateway_request_cache_hit: bool = False
    gateway_request_fallback_used: bool = False
    gateway_request_guardrail_triggered: bool = False
    gateway_has_review: bool = False
    gateway_reviewed_at: object | None = None
    gateway_has_failure_repair: bool = False
    gateway_has_policy: bool = False
    gateway_policy_type: str | None = None
    gateway_policy_id: str | None = None
    gateway_policy_route: str | None = None
    gateway_policy_synced: bool = False
    gateway_is_sample_only: bool = False
    gateway_sample_request_count: int = 0
    gateway_permission_limited: bool = False
    gateway_guard_blocked: bool = False
    gateway_first_loop_completed: bool = False
    gateway_signals: GatewayOnboardingSignals = field(
        default_factory=GatewayOnboardingSignals
    )
    voice_agents: int = 0
    voice_simulations: int = 0
    voice_calls: int = 0
    voice_reviews: int = 0
    voice_agent_id: str | None = None
    voice_agent_name: str | None = None
    voice_agent_provider: str | None = None
    voice_agent_version_id: str | None = None
    voice_scenario_id: str | None = None
    voice_run_test_id: str | None = None
    voice_test_execution_id: str | None = None
    voice_call_execution_id: str | None = None
    voice_call_status: str | None = None
    voice_call_completed_at: object | None = None
    voice_call_duration_seconds: int | None = None
    voice_call_response_time_ms: int | None = None
    voice_call_interruption_count: int | None = None
    voice_transcript_available: bool = False
    voice_recording_available: bool = False
    voice_has_agent: bool = False
    voice_has_scenario: bool = False
    voice_has_test: bool = False
    voice_has_call: bool = False
    voice_has_completed_call: bool = False
    voice_call_failed: bool = False
    voice_has_review: bool = False
    voice_has_success_criteria: bool = False
    voice_first_loop_completed: bool = False
    voice_is_sample_only: bool = False
    voice_sample_call_count: int = 0
    voice_permission_limited: bool = False
    voice_signals: VoiceOnboardingSignals = field(
        default_factory=VoiceOnboardingSignals
    )
    team_invites: int = 0
    dashboards: int = 0
    alerts: int = 0
    first_trace_id: str | None = None
    first_observe_id: str | None = None
    observe_project_exists: bool = False
    trace_exists: bool = False
    trace_reviewed: bool = False
    sample_project_opened: bool = False
    sample_trace_available: bool = False
    sample_signal_viewed: bool = False
    sample_trace_reviewed: bool = False
    evaluator_exists: bool = False
    dashboard_exists: bool = False
    alert_exists: bool = False
    saved_view_exists: bool = False
    first_loop_completed: bool = False
    useful_daily_signal: bool = False
    last_meaningful_event: object | None = None

    def to_payload(self):
        return {
            "provider_keys": self.provider_keys,
            "datasets": self.datasets,
            "evals": self.evals,
            "eval_runs": self.eval_runs,
            "eval_source_count": self.eval_source_count,
            "eval_source_type": self.eval_source_type,
            "eval_source_id": self.eval_source_id,
            "eval_source_name": self.eval_source_name,
            "eval_scorer_count": self.eval_scorer_count,
            "eval_scorer_id": self.eval_scorer_id,
            "eval_scorer_template_id": self.eval_scorer_template_id,
            "eval_scorer_name": self.eval_scorer_name,
            "eval_group_count": self.eval_group_count,
            "eval_group_id": self.eval_group_id,
            "eval_run_count": self.eval_run_count,
            "eval_run_id": self.eval_run_id,
            "eval_run_status": self.eval_run_status,
            "eval_run_completed_at": self.eval_run_completed_at,
            "eval_failure_count": self.eval_failure_count,
            "eval_has_source": self.eval_has_source,
            "eval_has_scorer": self.eval_has_scorer,
            "eval_has_completed_run": self.eval_has_completed_run,
            "eval_has_failures": self.eval_has_failures,
            "eval_has_review": self.eval_has_review,
            "eval_has_failure_action": self.eval_has_failure_action,
            "eval_first_loop_completed": self.eval_first_loop_completed,
            "eval_is_sample_only": self.eval_is_sample_only,
            "eval_sample_source_count": self.eval_sample_source_count,
            "eval_permission_limited": self.eval_permission_limited,
            "prompt_templates": self.prompt_templates,
            "prompt_versions": self.prompt_versions,
            "prompt_comparisons": self.prompt_comparisons,
            "first_prompt_id": self.first_prompt_id,
            "latest_prompt_id": self.latest_prompt_id,
            "prompt_sample_templates": self.prompt_sample_templates,
            "prompt_comparable_versions_exist": self.prompt_comparable_versions_exist,
            "prompt_run_exists": self.prompt_run_exists,
            "prompt_committed_version_exists": self.prompt_committed_version_exists,
            "prompt_comparison_completed": self.prompt_comparison_completed,
            "prompt_next_loop_action_exists": self.prompt_next_loop_action_exists,
            "prompt_first_loop_completed": self.prompt_first_loop_completed,
            "agents": self.agents,
            "agent_prototype_runs": self.agent_prototype_runs,
            "agent_id": self.agent_id,
            "agent_source": self.agent_source,
            "agent_version_id": self.agent_version_id,
            "agent_scenario_id": self.agent_scenario_id,
            "agent_test_id": self.agent_test_id,
            "agent_execution_id": self.agent_execution_id,
            "agent_call_execution_id": self.agent_call_execution_id,
            "agent_graph_execution_id": self.agent_graph_execution_id,
            "agent_run_status": self.agent_run_status,
            "agent_sample_count": self.agent_sample_count,
            "agent_has_agent": self.agent_has_agent,
            "agent_has_agent_version": self.agent_has_agent_version,
            "agent_has_step": self.agent_has_step,
            "agent_has_scenario": self.agent_has_scenario,
            "agent_has_run": self.agent_has_run,
            "agent_run_failed": self.agent_run_failed,
            "agent_has_review": self.agent_has_review,
            "agent_has_eval_coverage": self.agent_has_eval_coverage,
            "agent_multiple_scenarios": self.agent_multiple_scenarios,
            "agent_first_loop_completed": self.agent_first_loop_completed,
            "agent_voice_feature_unavailable": self.agent_voice_feature_unavailable,
            "agent_permission_limited": self.agent_permission_limited,
            "observe_projects": self.observe_projects,
            "traces": self.traces,
            "trace_reviews": self.trace_reviews,
            "gateway_keys": self.gateway_keys,
            "gateway_requests": self.gateway_requests,
            "gateway_policies": self.gateway_policies,
            "gateway_available": self.gateway_available,
            "gateway_id": self.gateway_id,
            "gateway_status": self.gateway_status,
            "gateway_public_url": self.gateway_public_url,
            "gateway_provider_count": self.gateway_provider_count,
            "gateway_provider_credential_id": self.gateway_provider_credential_id,
            "gateway_provider_name": self.gateway_provider_name,
            "gateway_provider_health_status": self.gateway_provider_health_status,
            "gateway_provider_model_count": self.gateway_provider_model_count,
            "gateway_has_provider": self.gateway_has_provider,
            "gateway_has_key": self.gateway_has_key,
            "gateway_key_id": self.gateway_key_id,
            "gateway_key_prefix": self.gateway_key_prefix,
            "gateway_key_status": self.gateway_key_status,
            "gateway_has_request": self.gateway_has_request,
            "gateway_request_log_id": self.gateway_request_log_id,
            "gateway_request_id": self.gateway_request_id,
            "gateway_request_status_code": self.gateway_request_status_code,
            "gateway_request_is_error": self.gateway_request_is_error,
            "gateway_request_error_message": self.gateway_request_error_message,
            "gateway_request_provider": self.gateway_request_provider,
            "gateway_request_model": self.gateway_request_model,
            "gateway_request_resolved_model": self.gateway_request_resolved_model,
            "gateway_request_latency_ms": self.gateway_request_latency_ms,
            "gateway_request_cost": self.gateway_request_cost,
            "gateway_request_cache_hit": self.gateway_request_cache_hit,
            "gateway_request_fallback_used": self.gateway_request_fallback_used,
            "gateway_request_guardrail_triggered": (
                self.gateway_request_guardrail_triggered
            ),
            "gateway_has_review": self.gateway_has_review,
            "gateway_reviewed_at": self.gateway_reviewed_at,
            "gateway_has_failure_repair": self.gateway_has_failure_repair,
            "gateway_has_policy": self.gateway_has_policy,
            "gateway_policy_type": self.gateway_policy_type,
            "gateway_policy_id": self.gateway_policy_id,
            "gateway_policy_route": self.gateway_policy_route,
            "gateway_policy_synced": self.gateway_policy_synced,
            "gateway_is_sample_only": self.gateway_is_sample_only,
            "gateway_sample_request_count": self.gateway_sample_request_count,
            "gateway_permission_limited": self.gateway_permission_limited,
            "gateway_guard_blocked": self.gateway_guard_blocked,
            "gateway_first_loop_completed": self.gateway_first_loop_completed,
            "voice_agents": self.voice_agents,
            "voice_simulations": self.voice_simulations,
            "voice_calls": self.voice_calls,
            "voice_reviews": self.voice_reviews,
            "voice_agent_id": self.voice_agent_id,
            "voice_agent_name": self.voice_agent_name,
            "voice_agent_provider": self.voice_agent_provider,
            "voice_agent_version_id": self.voice_agent_version_id,
            "voice_scenario_id": self.voice_scenario_id,
            "voice_run_test_id": self.voice_run_test_id,
            "voice_test_execution_id": self.voice_test_execution_id,
            "voice_call_execution_id": self.voice_call_execution_id,
            "voice_call_status": self.voice_call_status,
            "voice_call_completed_at": self.voice_call_completed_at,
            "voice_call_duration_seconds": self.voice_call_duration_seconds,
            "voice_call_response_time_ms": self.voice_call_response_time_ms,
            "voice_call_interruption_count": self.voice_call_interruption_count,
            "voice_transcript_available": self.voice_transcript_available,
            "voice_recording_available": self.voice_recording_available,
            "voice_has_agent": self.voice_has_agent,
            "voice_has_scenario": self.voice_has_scenario,
            "voice_has_test": self.voice_has_test,
            "voice_has_call": self.voice_has_call,
            "voice_has_completed_call": self.voice_has_completed_call,
            "voice_call_failed": self.voice_call_failed,
            "voice_has_review": self.voice_has_review,
            "voice_has_success_criteria": self.voice_has_success_criteria,
            "voice_first_loop_completed": self.voice_first_loop_completed,
            "voice_is_sample_only": self.voice_is_sample_only,
            "voice_sample_call_count": self.voice_sample_call_count,
            "voice_permission_limited": self.voice_permission_limited,
            "team_invites": self.team_invites,
            "dashboards": self.dashboards,
            "alerts": self.alerts,
            "first_trace_id": self.first_trace_id,
            "first_observe_id": self.first_observe_id,
            "sample_project_opened": self.sample_project_opened,
            "sample_trace_available": self.sample_trace_available,
            "sample_signal_viewed": self.sample_signal_viewed,
            "sample_trace_reviewed": self.sample_trace_reviewed,
        }


def _compatibility_checks(user, organization, workspace):
    from accounts.views.user import get_user_checks

    return get_user_checks(user, organization=organization, workspace=workspace)


def _observe_project_queryset(organization, workspace):
    from tracer.models.project import Project

    return (
        Project.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
            trace_type="observe",
        )
        .exclude(source__in=NON_REAL_OBSERVE_PROJECT_SOURCES)
        .filter(Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False))
    )


def _trace_queryset(organization, workspace):
    from tracer.models.trace import Trace

    return (
        Trace.no_workspace_objects.filter(
            project__organization=organization,
            project__workspace=workspace,
            project__trace_type="observe",
        )
        .exclude(project__source__in=NON_REAL_OBSERVE_PROJECT_SOURCES)
        .filter(
            Q(project__metadata__is_sample__isnull=True)
            | Q(project__metadata__is_sample=False)
        )
        .filter(Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False))
    )


def _custom_eval_exists(project_ids):
    if not project_ids:
        return False
    from tracer.models.custom_eval_config import CustomEvalConfig

    return CustomEvalConfig.no_workspace_objects.filter(
        project_id__in=project_ids,
    ).exists()


def _observe_eval_loop_completed(organization, workspace, project_ids):
    if not project_ids:
        return False
    from accounts.models import OnboardingActivationEvent

    project_ids = [str(project_id) for project_id in project_ids]
    return OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        event_name="first_quality_loop_completed",
        product_path="evals",
        is_sample=False,
        metadata__source_type="trace_project",
        metadata__source_id__in=project_ids,
    ).exists()


def _dashboard_exists(workspace):
    from tracer.models.dashboard import Dashboard

    return Dashboard.no_workspace_objects.filter(workspace=workspace).exists()


def _alert_exists(organization, workspace, project_ids):
    from tracer.models.monitor import UserAlertMonitor

    queryset = UserAlertMonitor.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
    )
    if project_ids:
        queryset = queryset.filter(project_id__in=project_ids)
    return queryset.exists()


def _saved_view_exists(workspace, project_ids):
    from tracer.models.saved_view import SavedView

    queryset = SavedView.no_workspace_objects.filter(workspace=workspace)
    if project_ids:
        queryset = queryset.filter(project_id__in=project_ids)
    return queryset.exists()


def _as_count(value):
    return 1 if value else 0


REAL_PRODUCT_SETUP_SIGNAL_FIELDS = (
    "first_loop_completed",
    "prompt_first_loop_completed",
    "agent_first_loop_completed",
    "gateway_first_loop_completed",
    "eval_first_loop_completed",
    "voice_first_loop_completed",
)


def signals_have_real_product_setup(signals):
    return any(
        bool(getattr(signals, field_name, False))
        for field_name in REAL_PRODUCT_SETUP_SIGNAL_FIELDS
    )


def collect_onboarding_signals(*, user, organization, workspace):
    if not organization or not workspace:
        return OnboardingSignals(first_checks={})

    first_checks = _compatibility_checks(user, organization, workspace)

    observe_projects = _observe_project_queryset(organization, workspace)
    observe_project_ids = list(observe_projects.values_list("id", flat=True)[:20])
    first_observe_id = str(observe_project_ids[0]) if observe_project_ids else None

    traces = _trace_queryset(organization, workspace)
    first_trace = traces.only("id", "project_id").order_by("created_at").first()
    first_trace_id = str(first_trace.id) if first_trace else None

    trace_reviewed = has_event(
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        is_sample=False,
    )
    sample_project_opened = has_event(
        organization=organization,
        workspace=workspace,
        event_name="onboarding_sample_project_opened",
        is_sample=True,
    )
    sample_trace_available = has_event(
        organization=organization,
        workspace=workspace,
        event_name="sample_trace_available",
        is_sample=True,
    )
    sample_signal_viewed = has_event(
        organization=organization,
        workspace=workspace,
        event_name="sample_signal_viewed",
        is_sample=True,
    )
    sample_trace_reviewed = (
        has_event(
            organization=organization,
            workspace=workspace,
            event_name="trace_reviewed",
            is_sample=True,
        )
        or sample_signal_viewed
    )

    evaluator_exists = _custom_eval_exists(observe_project_ids)
    dashboard_exists = _dashboard_exists(workspace)
    alert_exists = _alert_exists(organization, workspace, observe_project_ids)
    saved_view_exists = _saved_view_exists(workspace, observe_project_ids)
    improvement_exists = (
        evaluator_exists or dashboard_exists or alert_exists or saved_view_exists
    )
    observe_eval_loop_completed = _observe_eval_loop_completed(
        organization,
        workspace,
        observe_project_ids,
    )
    improvement_exists = improvement_exists or observe_eval_loop_completed
    real_loop_completed = first_quality_loop_completed(
        organization=organization,
        workspace=workspace,
        product_path="observe",
    ) or (trace_reviewed and improvement_exists)
    last_event = latest_event(
        organization=organization,
        workspace=workspace,
        is_sample=False,
    )
    prompt_signals = collect_prompt_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    agent_signals = collect_agent_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    eval_signals = collect_eval_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    gateway_signals = collect_gateway_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    voice_signals = collect_voice_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    return OnboardingSignals(
        first_checks=first_checks,
        provider_keys=_as_count(first_checks.get("keys")),
        datasets=max(_as_count(first_checks.get("dataset")), eval_signals.source_count),
        evals=max(_as_count(first_checks.get("evaluation")), eval_signals.scorer_count),
        eval_runs=max(
            _as_count(first_checks.get("experiment")), eval_signals.run_count
        ),
        eval_source_count=eval_signals.source_count,
        eval_source_type=eval_signals.source_type,
        eval_source_id=eval_signals.source_id,
        eval_source_name=eval_signals.source_name,
        eval_scorer_count=eval_signals.scorer_count,
        eval_scorer_id=eval_signals.scorer_id,
        eval_scorer_template_id=eval_signals.scorer_template_id,
        eval_scorer_name=eval_signals.scorer_name,
        eval_group_count=eval_signals.eval_group_count,
        eval_group_id=eval_signals.eval_group_id,
        eval_run_count=eval_signals.run_count,
        eval_run_id=eval_signals.run_id,
        eval_run_status=eval_signals.run_status,
        eval_run_completed_at=eval_signals.run_completed_at,
        eval_failure_count=eval_signals.failure_count,
        eval_has_source=eval_signals.has_source,
        eval_has_scorer=eval_signals.has_scorer,
        eval_has_completed_run=eval_signals.has_completed_run,
        eval_has_failures=eval_signals.has_failures,
        eval_has_review=eval_signals.has_review,
        eval_has_failure_action=eval_signals.has_failure_action,
        eval_first_loop_completed=eval_signals.first_loop_completed,
        eval_is_sample_only=eval_signals.is_sample_only,
        eval_sample_source_count=eval_signals.sample_source_count,
        eval_permission_limited=eval_signals.permission_limited,
        eval_signals=eval_signals,
        prompt_templates=prompt_signals.prompt_count,
        prompt_versions=prompt_signals.committed_version_count
        + prompt_signals.draft_version_count,
        prompt_comparisons=_as_count(prompt_signals.comparison_completed),
        first_prompt_id=prompt_signals.first_prompt_id,
        latest_prompt_id=prompt_signals.latest_prompt_id,
        latest_prompt_name=prompt_signals.latest_prompt_name,
        prompt_sample_templates=prompt_signals.sample_prompt_count,
        prompt_run_exists=prompt_signals.has_test_run,
        prompt_committed_version_exists=prompt_signals.has_committed_version,
        prompt_comparable_versions_exist=prompt_signals.has_comparable_versions,
        prompt_comparison_completed=prompt_signals.comparison_completed,
        prompt_next_loop_action_exists=prompt_signals.has_next_loop_action,
        prompt_first_loop_completed=prompt_signals.first_loop_completed,
        prompt_signals=prompt_signals,
        agents=agent_signals.agent_count,
        agent_prototype_runs=agent_signals.run_count,
        agent_id=agent_signals.agent_id,
        agent_source=agent_signals.agent_source,
        agent_version_id=agent_signals.agent_version_id,
        agent_scenario_id=agent_signals.scenario_id,
        agent_test_id=agent_signals.test_id,
        agent_execution_id=agent_signals.execution_id,
        agent_call_execution_id=agent_signals.call_execution_id,
        agent_graph_execution_id=agent_signals.graph_execution_id,
        agent_run_status=agent_signals.run_status,
        agent_sample_count=agent_signals.sample_agent_count,
        agent_has_agent=agent_signals.has_agent,
        agent_has_agent_version=agent_signals.has_agent_version,
        agent_has_step=agent_signals.has_step,
        agent_has_scenario=agent_signals.has_scenario,
        agent_has_run=agent_signals.has_run,
        agent_run_failed=agent_signals.run_failed,
        agent_has_review=agent_signals.has_review,
        agent_has_eval_coverage=agent_signals.has_eval_coverage,
        agent_multiple_scenarios=agent_signals.has_multiple_scenarios,
        agent_first_loop_completed=agent_signals.first_loop_completed,
        agent_voice_feature_unavailable=agent_signals.voice_feature_unavailable,
        agent_permission_limited=agent_signals.permission_limited,
        agent_signals=agent_signals,
        observe_projects=_as_count(bool(observe_project_ids)),
        traces=_as_count(first_trace is not None),
        trace_reviews=_as_count(trace_reviewed),
        gateway_keys=gateway_signals.key_count,
        gateway_requests=gateway_signals.request_count,
        gateway_policies=gateway_signals.policy_count,
        gateway_available=gateway_signals.gateway_available,
        gateway_id=gateway_signals.gateway_id,
        gateway_status=gateway_signals.gateway_status,
        gateway_public_url=gateway_signals.gateway_public_url,
        gateway_provider_count=gateway_signals.provider_count,
        gateway_provider_credential_id=gateway_signals.provider_credential_id,
        gateway_provider_name=gateway_signals.provider_name,
        gateway_provider_health_status=gateway_signals.provider_health_status,
        gateway_provider_model_count=gateway_signals.provider_model_count,
        gateway_has_provider=gateway_signals.has_provider,
        gateway_has_key=gateway_signals.has_key,
        gateway_key_id=gateway_signals.gateway_key_id,
        gateway_key_prefix=gateway_signals.key_prefix,
        gateway_key_status=gateway_signals.key_status,
        gateway_has_request=gateway_signals.has_request,
        gateway_request_log_id=gateway_signals.request_log_id,
        gateway_request_id=gateway_signals.request_id,
        gateway_request_status_code=gateway_signals.request_status_code,
        gateway_request_is_error=gateway_signals.request_failed,
        gateway_request_error_message=gateway_signals.request_error_message,
        gateway_request_provider=gateway_signals.request_provider,
        gateway_request_model=gateway_signals.request_model,
        gateway_request_resolved_model=gateway_signals.request_resolved_model,
        gateway_request_latency_ms=gateway_signals.request_latency_ms,
        gateway_request_cost=gateway_signals.request_cost,
        gateway_request_cache_hit=gateway_signals.request_cache_hit,
        gateway_request_fallback_used=gateway_signals.request_fallback_used,
        gateway_request_guardrail_triggered=(
            gateway_signals.request_guardrail_triggered
        ),
        gateway_has_review=gateway_signals.has_review,
        gateway_reviewed_at=gateway_signals.reviewed_at,
        gateway_has_failure_repair=gateway_signals.has_failure_repair,
        gateway_has_policy=gateway_signals.has_policy,
        gateway_policy_type=gateway_signals.policy_type,
        gateway_policy_id=gateway_signals.policy_id,
        gateway_policy_route=gateway_signals.policy_route,
        gateway_policy_synced=gateway_signals.policy_synced,
        gateway_is_sample_only=gateway_signals.is_sample_only,
        gateway_sample_request_count=gateway_signals.sample_request_count,
        gateway_permission_limited=gateway_signals.permission_limited,
        gateway_guard_blocked=gateway_signals.guard_blocked,
        gateway_first_loop_completed=gateway_signals.first_loop_completed,
        gateway_signals=gateway_signals,
        voice_agents=voice_signals.agent_count,
        voice_simulations=voice_signals.run_test_count,
        voice_calls=voice_signals.call_count,
        voice_reviews=1 if voice_signals.has_review else 0,
        voice_agent_id=voice_signals.agent_id,
        voice_agent_name=voice_signals.agent_name,
        voice_agent_provider=voice_signals.agent_provider,
        voice_agent_version_id=voice_signals.agent_version_id,
        voice_scenario_id=voice_signals.scenario_id,
        voice_run_test_id=voice_signals.run_test_id,
        voice_test_execution_id=voice_signals.test_execution_id,
        voice_call_execution_id=voice_signals.call_execution_id,
        voice_call_status=voice_signals.call_status,
        voice_call_completed_at=voice_signals.call_completed_at,
        voice_call_duration_seconds=voice_signals.call_duration_seconds,
        voice_call_response_time_ms=voice_signals.call_response_time_ms,
        voice_call_interruption_count=voice_signals.call_interruption_count,
        voice_transcript_available=voice_signals.transcript_available,
        voice_recording_available=voice_signals.recording_available,
        voice_has_agent=voice_signals.has_agent,
        voice_has_scenario=voice_signals.has_scenario,
        voice_has_test=voice_signals.has_test,
        voice_has_call=voice_signals.has_call,
        voice_has_completed_call=voice_signals.has_completed_call,
        voice_call_failed=voice_signals.call_failed,
        voice_has_review=voice_signals.has_review,
        voice_has_success_criteria=voice_signals.has_success_criteria,
        voice_first_loop_completed=voice_signals.first_loop_completed,
        voice_is_sample_only=voice_signals.is_sample_only,
        voice_sample_call_count=voice_signals.sample_call_count,
        voice_permission_limited=voice_signals.permission_limited,
        voice_signals=voice_signals,
        team_invites=_as_count(first_checks.get("invite")),
        dashboards=_as_count(dashboard_exists),
        alerts=_as_count(alert_exists),
        first_trace_id=first_trace_id,
        first_observe_id=first_observe_id,
        observe_project_exists=bool(observe_project_ids),
        trace_exists=first_trace is not None,
        trace_reviewed=trace_reviewed,
        sample_project_opened=sample_project_opened,
        sample_trace_available=sample_trace_available,
        sample_signal_viewed=sample_signal_viewed,
        sample_trace_reviewed=sample_trace_reviewed,
        evaluator_exists=evaluator_exists,
        dashboard_exists=dashboard_exists,
        alert_exists=alert_exists,
        saved_view_exists=saved_view_exists,
        first_loop_completed=real_loop_completed,
        useful_daily_signal=real_loop_completed and first_trace is not None,
        last_meaningful_event=last_event,
    )
