import uuid

from rest_framework import serializers

from accounts.services.onboarding.constants import (
    ACTION_KINDS,
    ACTIVATION_SCHEMA_VERSION,
    ACTIVATION_STAGES,
    AVAILABLE_PATH_STATUSES,
    EMAIL_CONTEXT_STATUSES,
    HOME_MODES,
    LIFECYCLE_ELIGIBILITY_STATES,
    LIFECYCLE_SUPPRESSION_REASONS,
    ONBOARDING_ACTIVATION_EVENTS,
    ONBOARDING_GOALS,
    PRODUCT_PATHS,
    PROGRESS_STATES,
    ROUTE_UNAVAILABLE_REASONS,
    SAMPLE_PROJECT_STATUSES,
    canonical_activation_event,
    canonical_goal,
    canonical_path,
    choices,
)
from accounts.services.onboarding.goals import goal_to_primary_path
from accounts.services.onboarding.journey_plan import JOURNEY_STEP_STATUSES


class ActivationAnalyticsSerializer(serializers.Serializer):
    event_name = serializers.CharField()
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    target_path = serializers.ChoiceField(
        choices=choices(PRODUCT_PATHS),
        required=False,
        allow_null=True,
    )


class ActivationActionSerializer(serializers.Serializer):
    id = serializers.CharField()
    kind = serializers.ChoiceField(choices=choices(ACTION_KINDS))
    title = serializers.CharField()
    description = serializers.CharField()
    href = serializers.CharField(allow_blank=True, allow_null=True)
    cta_label = serializers.CharField()
    estimated_minutes = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
    )
    priority = serializers.IntegerField()
    blocked = serializers.BooleanField()
    blocked_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    requires_permission = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    completion_event = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    is_sample = serializers.BooleanField(default=False)
    route_available = serializers.BooleanField()
    fallback_href = serializers.CharField()
    analytics = ActivationAnalyticsSerializer()

    def validate(self, attrs):
        if not attrs["blocked"] and not attrs.get("href"):
            raise serializers.ValidationError(
                "Unblocked actions must include an internal href."
            )
        if attrs["blocked"] and not attrs.get("blocked_reason"):
            raise serializers.ValidationError(
                "Blocked actions must include a blocked_reason."
            )
        if not attrs.get("fallback_href"):
            raise serializers.ValidationError("Actions must include a fallback_href.")
        if attrs["is_sample"] and attrs.get("completion_event"):
            completion_event = attrs["completion_event"]
            if "sample" not in completion_event:
                raise serializers.ValidationError(
                    "Sample actions cannot use real activation completion events."
                )
        return attrs


class ActivationProgressSerializer(serializers.Serializer):
    build = serializers.ChoiceField(choices=choices(PROGRESS_STATES))
    test = serializers.ChoiceField(choices=choices(PROGRESS_STATES))
    observe = serializers.ChoiceField(choices=choices(PROGRESS_STATES))
    ship = serializers.ChoiceField(choices=choices(PROGRESS_STATES))
    improve = serializers.ChoiceField(choices=choices(PROGRESS_STATES))


class ActivationStageCopySerializer(serializers.Serializer):
    eyebrow = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()


class ActivationSignalsSerializer(serializers.Serializer):
    provider_keys = serializers.IntegerField(min_value=0, default=0)
    datasets = serializers.IntegerField(min_value=0, default=0)
    evals = serializers.IntegerField(min_value=0, default=0)
    eval_runs = serializers.IntegerField(min_value=0, default=0)
    eval_source_count = serializers.IntegerField(min_value=0, default=0)
    eval_source_type = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_source_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_source_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_scorer_count = serializers.IntegerField(min_value=0, default=0)
    eval_scorer_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_scorer_template_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_scorer_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_group_count = serializers.IntegerField(min_value=0, default=0)
    eval_group_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_run_count = serializers.IntegerField(min_value=0, default=0)
    eval_run_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_run_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_run_completed_at = serializers.DateTimeField(required=False, allow_null=True)
    eval_failure_count = serializers.IntegerField(min_value=0, default=0)
    eval_has_source = serializers.BooleanField(default=False)
    eval_has_scorer = serializers.BooleanField(default=False)
    eval_has_completed_run = serializers.BooleanField(default=False)
    eval_has_failures = serializers.BooleanField(default=False)
    eval_has_review = serializers.BooleanField(default=False)
    eval_has_failure_action = serializers.BooleanField(default=False)
    eval_first_loop_completed = serializers.BooleanField(default=False)
    eval_is_sample_only = serializers.BooleanField(default=False)
    eval_sample_source_count = serializers.IntegerField(min_value=0, default=0)
    eval_permission_limited = serializers.BooleanField(default=False)
    prompt_templates = serializers.IntegerField(min_value=0, default=0)
    prompt_versions = serializers.IntegerField(min_value=0, default=0)
    prompt_comparisons = serializers.IntegerField(min_value=0, default=0)
    first_prompt_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    latest_prompt_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    prompt_sample_templates = serializers.IntegerField(min_value=0, default=0)
    agents = serializers.IntegerField(min_value=0, default=0)
    agent_prototype_runs = serializers.IntegerField(min_value=0, default=0)
    agent_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_source = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_version_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_scenario_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_test_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_call_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_graph_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_run_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_sample_count = serializers.IntegerField(min_value=0, default=0)
    agent_has_agent = serializers.BooleanField(default=False)
    agent_has_agent_version = serializers.BooleanField(default=False)
    agent_has_scenario = serializers.BooleanField(default=False)
    agent_has_run = serializers.BooleanField(default=False)
    agent_run_failed = serializers.BooleanField(default=False)
    agent_has_review = serializers.BooleanField(default=False)
    agent_has_eval_coverage = serializers.BooleanField(default=False)
    agent_multiple_scenarios = serializers.BooleanField(default=False)
    agent_first_loop_completed = serializers.BooleanField(default=False)
    agent_voice_feature_unavailable = serializers.BooleanField(default=False)
    observe_projects = serializers.IntegerField(min_value=0, default=0)
    traces = serializers.IntegerField(min_value=0, default=0)
    trace_reviews = serializers.IntegerField(min_value=0, default=0)
    gateway_keys = serializers.IntegerField(min_value=0, default=0)
    gateway_requests = serializers.IntegerField(min_value=0, default=0)
    gateway_policies = serializers.IntegerField(min_value=0, default=0)
    gateway_available = serializers.BooleanField(default=False)
    gateway_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_public_url = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_provider_count = serializers.IntegerField(min_value=0, default=0)
    gateway_provider_credential_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_provider_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_provider_health_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_provider_model_count = serializers.IntegerField(min_value=0, default=0)
    gateway_has_provider = serializers.BooleanField(default=False)
    gateway_has_key = serializers.BooleanField(default=False)
    gateway_key_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_key_prefix = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_key_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_has_request = serializers.BooleanField(default=False)
    gateway_request_log_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_status_code = serializers.IntegerField(
        required=False,
        allow_null=True,
    )
    gateway_request_is_error = serializers.BooleanField(default=False)
    gateway_request_error_message = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_provider = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_model = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_resolved_model = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_latency_ms = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    gateway_request_cost = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_request_cache_hit = serializers.BooleanField(default=False)
    gateway_request_fallback_used = serializers.BooleanField(default=False)
    gateway_request_guardrail_triggered = serializers.BooleanField(default=False)
    gateway_has_review = serializers.BooleanField(default=False)
    gateway_reviewed_at = serializers.DateTimeField(required=False, allow_null=True)
    gateway_has_failure_repair = serializers.BooleanField(default=False)
    gateway_has_policy = serializers.BooleanField(default=False)
    gateway_policy_type = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_policy_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_policy_route = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_policy_synced = serializers.BooleanField(default=False)
    gateway_is_sample_only = serializers.BooleanField(default=False)
    gateway_sample_request_count = serializers.IntegerField(min_value=0, default=0)
    gateway_permission_limited = serializers.BooleanField(default=False)
    gateway_guard_blocked = serializers.BooleanField(default=False)
    gateway_first_loop_completed = serializers.BooleanField(default=False)
    voice_agents = serializers.IntegerField(min_value=0, default=0)
    voice_simulations = serializers.IntegerField(min_value=0, default=0)
    voice_calls = serializers.IntegerField(min_value=0, default=0)
    voice_reviews = serializers.IntegerField(min_value=0, default=0)
    voice_agent_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_agent_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_agent_provider = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_agent_version_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_scenario_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_run_test_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_test_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_call_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_call_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    voice_call_completed_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )
    voice_call_duration_seconds = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    voice_call_response_time_ms = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    voice_call_interruption_count = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    voice_transcript_available = serializers.BooleanField(default=False)
    voice_recording_available = serializers.BooleanField(default=False)
    voice_has_agent = serializers.BooleanField(default=False)
    voice_has_scenario = serializers.BooleanField(default=False)
    voice_has_test = serializers.BooleanField(default=False)
    voice_has_call = serializers.BooleanField(default=False)
    voice_has_completed_call = serializers.BooleanField(default=False)
    voice_call_failed = serializers.BooleanField(default=False)
    voice_has_review = serializers.BooleanField(default=False)
    voice_has_success_criteria = serializers.BooleanField(default=False)
    voice_first_loop_completed = serializers.BooleanField(default=False)
    voice_is_sample_only = serializers.BooleanField(default=False)
    voice_sample_call_count = serializers.IntegerField(min_value=0, default=0)
    voice_permission_limited = serializers.BooleanField(default=False)
    team_invites = serializers.IntegerField(min_value=0, default=0)
    dashboards = serializers.IntegerField(min_value=0, default=0)
    alerts = serializers.IntegerField(min_value=0, default=0)
    first_trace_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    first_observe_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    sample_project_opened = serializers.BooleanField(default=False)
    sample_trace_available = serializers.BooleanField(default=False)
    sample_signal_viewed = serializers.BooleanField(default=False)
    sample_trace_reviewed = serializers.BooleanField(default=False)


class AvailablePathSerializer(serializers.Serializer):
    id = serializers.ChoiceField(choices=choices(PRODUCT_PATHS))
    label = serializers.CharField()
    description = serializers.CharField()
    status = serializers.ChoiceField(choices=choices(AVAILABLE_PATH_STATUSES))
    href = serializers.CharField()
    is_available = serializers.BooleanField()
    blocked_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    requires_permission = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    first_action_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class ActivationJourneyStepSerializer(serializers.Serializer):
    id = serializers.CharField()
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    action_id = serializers.CharField()
    success_event = serializers.ChoiceField(
        choices=choices(ONBOARDING_ACTIVATION_EVENTS),
        required=False,
    )
    tour_anchor = serializers.CharField(
        required=False,
        allow_blank=True,
    )
    label = serializers.CharField()
    description = serializers.CharField()
    status = serializers.ChoiceField(choices=choices(JOURNEY_STEP_STATUSES))
    href = serializers.CharField(allow_blank=True)
    fallback_href = serializers.CharField()
    route_available = serializers.BooleanField()
    blocked_reason = serializers.CharField(
        required=False,
        allow_blank=True,
    )
    requires_permission = serializers.CharField(
        required=False,
        allow_blank=True,
    )


class ActivationJourneyPlanSerializer(serializers.Serializer):
    id = serializers.CharField()
    primary_path = serializers.ChoiceField(choices=choices(PRODUCT_PATHS))
    eyebrow = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    chips = serializers.ListField(child=serializers.CharField())
    current_step_id = serializers.CharField()
    current_step_index = serializers.IntegerField(
        min_value=0,
    )
    steps = ActivationJourneyStepSerializer(many=True)


class AvailableGoalSerializer(serializers.Serializer):
    id = serializers.CharField()
    goal = serializers.ChoiceField(choices=choices(ONBOARDING_GOALS))
    primary_path = serializers.ChoiceField(choices=choices(PRODUCT_PATHS))
    label = serializers.CharField()
    description = serializers.CharField()
    estimated_minutes = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
    )
    disabled = serializers.BooleanField(default=False)
    disabled_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class SampleProjectStateSerializer(serializers.Serializer):
    available = serializers.BooleanField()
    created = serializers.BooleanField()
    status = serializers.ChoiceField(choices=choices(SAMPLE_PROJECT_STATUSES))
    href = serializers.CharField(allow_blank=True, allow_null=True)
    version = serializers.CharField(allow_blank=True, allow_null=True)
    manifest_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    manifest_version = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    label = serializers.CharField(required=False, allow_blank=True, default="Sample")
    entry_route = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    is_repairable = serializers.BooleanField(required=False, default=False)
    blocked_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    artifact_refs = serializers.JSONField(required=False)
    health = serializers.JSONField(required=False)
    real_setup_href = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    is_hidden = serializers.BooleanField()
    hidden_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    entry_routes = serializers.ListField(child=serializers.CharField())
    missing_artifacts = serializers.ListField(child=serializers.CharField())
    last_opened_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        status = attrs["status"]
        if attrs["created"] and status not in {
            "ready",
            "partial",
            "partially_ready",
            "ready_for_observe",
            "stale_manifest",
            "repair_required",
            "repair_failed",
            "hidden",
            "creating",
            "unavailable",
        }:
            raise serializers.ValidationError(
                "Created samples must be ready, partial, stale, or repairable."
            )
        if status in {"partial", "partially_ready"} and not (
            attrs["missing_artifacts"] or attrs["entry_routes"]
        ):
            raise serializers.ValidationError(
                "Partial samples must list missing_artifacts or entry_routes."
            )
        if status in {"ready", "ready_for_observe"} and not attrs["entry_routes"]:
            raise serializers.ValidationError("Ready samples must list entry_routes.")
        return attrs


class ActivationPromptStateSerializer(serializers.Serializer):
    prompt_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    prompt_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    has_real_prompt = serializers.BooleanField()
    has_test_run = serializers.BooleanField()
    has_committed_version = serializers.BooleanField()
    has_comparison = serializers.BooleanField()
    has_next_loop_action = serializers.BooleanField()
    is_sample = serializers.BooleanField(default=False)
    sample_prompt_count = serializers.IntegerField(min_value=0, default=0)
    diagnostics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate(self, attrs):
        if attrs["is_sample"] and attrs.get("has_real_prompt"):
            raise serializers.ValidationError(
                "Sample prompt state cannot count as a real prompt."
            )
        return attrs


class ActivationAgentStateSerializer(serializers.Serializer):
    agent_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_source = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_version_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    scenario_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    test_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    call_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    graph_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    run_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    run_completed_at = serializers.DateTimeField(required=False, allow_null=True)
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    has_agent = serializers.BooleanField()
    has_agent_version = serializers.BooleanField()
    has_scenario = serializers.BooleanField()
    has_run = serializers.BooleanField()
    has_review = serializers.BooleanField()
    has_eval_coverage = serializers.BooleanField()
    is_sample = serializers.BooleanField(default=False)
    sample_agent_count = serializers.IntegerField(min_value=0, default=0)
    voice_feature_unavailable = serializers.BooleanField(default=False)
    permission_limited = serializers.BooleanField(default=False)
    diagnostics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate(self, attrs):
        if attrs["is_sample"] and attrs.get("has_agent"):
            raise serializers.ValidationError(
                "Sample agent state cannot count as a real agent."
            )
        return attrs


class ActivationVoiceStateSerializer(serializers.Serializer):
    agent_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_provider = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_version_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    scenario_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    run_test_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    test_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    call_execution_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    call_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    call_completed_at = serializers.DateTimeField(required=False, allow_null=True)
    call_duration_seconds = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    call_response_time_ms = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    call_interruption_count = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    transcript_available = serializers.BooleanField(default=False)
    recording_available = serializers.BooleanField(default=False)
    reviewed_at = serializers.DateTimeField(required=False, allow_null=True)
    success_criteria_at = serializers.DateTimeField(required=False, allow_null=True)
    eval_config_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    has_agent = serializers.BooleanField(default=False)
    has_scenario = serializers.BooleanField(default=False)
    has_test = serializers.BooleanField(default=False)
    has_call = serializers.BooleanField(default=False)
    has_completed_call = serializers.BooleanField(default=False)
    call_failed = serializers.BooleanField(default=False)
    has_review = serializers.BooleanField(default=False)
    has_success_criteria = serializers.BooleanField(default=False)
    is_sample = serializers.BooleanField(default=False)
    sample_call_count = serializers.IntegerField(min_value=0, default=0)
    permission_limited = serializers.BooleanField(default=False)
    diagnostics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate(self, attrs):
        if attrs["is_sample"] and attrs.get("has_call"):
            raise serializers.ValidationError(
                "Sample voice call state cannot count as a real call."
            )
        if attrs.get("has_review") and not attrs.get("has_call"):
            raise serializers.ValidationError("Voice review requires a call.")
        if attrs.get("has_success_criteria") and not attrs.get("has_review"):
            raise serializers.ValidationError(
                "Voice success criteria require call review."
            )
        return attrs


class ActivationGatewayStateSerializer(serializers.Serializer):
    gateway_available = serializers.BooleanField()
    gateway_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    gateway_public_url = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    provider_count = serializers.IntegerField(min_value=0, default=0)
    provider_credential_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    provider_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    provider_health_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    provider_model_count = serializers.IntegerField(min_value=0, default=0)
    has_provider = serializers.BooleanField(default=False)
    has_key = serializers.BooleanField(default=False)
    gateway_key_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    key_prefix = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    key_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    has_request = serializers.BooleanField(default=False)
    request_log_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_status_code = serializers.IntegerField(
        required=False,
        allow_null=True,
    )
    request_is_error = serializers.BooleanField(default=False)
    request_error_message = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_provider = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_model = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_resolved_model = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_latency_ms = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
    )
    request_cost = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    request_cache_hit = serializers.BooleanField(default=False)
    request_fallback_used = serializers.BooleanField(default=False)
    request_guardrail_triggered = serializers.BooleanField(default=False)
    has_review = serializers.BooleanField(default=False)
    reviewed_at = serializers.DateTimeField(required=False, allow_null=True)
    has_failure_repair = serializers.BooleanField(default=False)
    has_policy = serializers.BooleanField(default=False)
    policy_type = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    policy_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    policy_route = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    policy_synced = serializers.BooleanField(default=False)
    is_sample = serializers.BooleanField(default=False)
    sample_request_count = serializers.IntegerField(min_value=0, default=0)
    permission_limited = serializers.BooleanField(default=False)
    guard_blocked = serializers.BooleanField(default=False)
    diagnostics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))

    def validate(self, attrs):
        if attrs["is_sample"] and attrs.get("has_request"):
            raise serializers.ValidationError(
                "Sample gateway request state cannot count as a real request."
            )
        return attrs


class ActivationEvalStateSerializer(serializers.Serializer):
    source_type = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    source_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    source_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    scorer_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    scorer_template_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    scorer_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eval_group_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    run_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    run_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    run_completed_at = serializers.DateTimeField(required=False, allow_null=True)
    failure_count = serializers.IntegerField(min_value=0, default=0)
    reviewed_at = serializers.DateTimeField(required=False, allow_null=True)
    failure_action_at = serializers.DateTimeField(required=False, allow_null=True)
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    has_source = serializers.BooleanField(default=False)
    has_scorer = serializers.BooleanField(default=False)
    has_completed_run = serializers.BooleanField(default=False)
    has_failures = serializers.BooleanField(default=False)
    has_review = serializers.BooleanField(default=False)
    has_failure_action = serializers.BooleanField(default=False)
    is_sample = serializers.BooleanField(default=False)
    sample_source_count = serializers.IntegerField(min_value=0, default=0)
    permission_limited = serializers.BooleanField(default=False)
    diagnostics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate(self, attrs):
        if attrs["is_sample"] and attrs.get("has_source"):
            raise serializers.ValidationError(
                "Sample eval source state cannot count as a real source."
            )
        if attrs.get("has_review") and not attrs.get("has_completed_run"):
            raise serializers.ValidationError("Eval review requires a completed run.")
        return attrs


class LifecycleEligibilitySerializer(serializers.Serializer):
    eligible = serializers.BooleanField()
    suppressed = serializers.BooleanField()
    suppression_reason = serializers.ChoiceField(
        choices=choices(LIFECYCLE_SUPPRESSION_REASONS),
        required=False,
        allow_null=True,
    )
    next_email_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    next_email_after = serializers.DateTimeField(required=False, allow_null=True)
    digest_eligible = serializers.BooleanField()
    last_email_sent_at = serializers.DateTimeField(required=False, allow_null=True)
    frequency_cap_remaining = serializers.IntegerField(min_value=0)
    dry_run_only = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if attrs["suppressed"] and not attrs.get("suppression_reason"):
            raise serializers.ValidationError(
                "Suppressed lifecycle decisions must include suppression_reason."
            )
        return attrs


class LifecyclePreviewSerializer(serializers.Serializer):
    dry_run_enabled = serializers.BooleanField()
    send_enabled = serializers.BooleanField(default=False)
    status = serializers.ChoiceField(choices=choices(LIFECYCLE_ELIGIBILITY_STATES))
    next_campaign_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    template_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eligible_at = serializers.DateTimeField(required=False, allow_null=True)
    suppressed = serializers.BooleanField()
    suppression_reason = serializers.ChoiceField(
        choices=choices(LIFECYCLE_SUPPRESSION_REASONS),
        required=False,
        allow_null=True,
    )
    target_success_event = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    target_action_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    target_url = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    dry_run_only = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if attrs["suppressed"] and not attrs.get("suppression_reason"):
            raise serializers.ValidationError(
                "Suppressed lifecycle decisions must include suppression_reason."
            )
        if attrs["send_enabled"] and not attrs["dry_run_enabled"]:
            raise serializers.ValidationError(
                "Lifecycle sends require dry-run evaluation."
            )
        return attrs


class DailyQualityWindowSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()


class DailyQualitySignalSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.ChoiceField(
        choices=choices(
            (
                "trace_failure",
                "span_latency",
                "span_cost",
                "agent_run_issue",
                "gateway_request_issue",
                "eval_failure",
                "voice_call_issue",
                "alert_triggered",
                "feed_issue",
                "dashboard_missing",
                "alert_missing",
                "evaluator_missing",
                "saved_view_missing",
            )
        )
    )
    severity = serializers.ChoiceField(choices=choices(("critical", "warning", "info")))
    title = serializers.CharField()
    body = serializers.CharField()
    source_type = serializers.CharField()
    source_id = serializers.CharField()
    project_id = serializers.CharField(required=False, allow_null=True)
    route = serializers.CharField()
    is_sample = serializers.BooleanField(default=False)
    created_at = serializers.DateTimeField()

    def validate(self, attrs):
        if attrs["is_sample"]:
            raise serializers.ValidationError(
                "Daily quality signals cannot come from sample data."
            )
        return attrs


class DailyQualityActionSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    body = serializers.CharField()
    route = serializers.CharField()
    fallback_route = serializers.CharField()
    route_available = serializers.BooleanField(default=True)
    source_type = serializers.CharField()
    source_id = serializers.CharField(required=False, allow_null=True)
    assigned_to_user_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    assigned_to_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    assigned_at = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    is_overdue = serializers.BooleanField(default=False)
    success_event = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    is_primary = serializers.BooleanField(default=False)
    is_sample = serializers.BooleanField(default=False)
    requires_permission = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    activation_kind = serializers.ChoiceField(
        choices=choices(ACTION_KINDS),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        if attrs["is_sample"]:
            raise serializers.ValidationError(
                "Daily quality actions cannot use sample data."
            )
        return attrs


class DailyQualityProductCardSerializer(serializers.Serializer):
    path = serializers.ChoiceField(choices=choices(PRODUCT_PATHS))
    status = serializers.CharField()
    label = serializers.CharField()
    summary = serializers.CharField()
    metric = serializers.CharField()
    change = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    route = serializers.CharField()


class DailyQualityWeeklyReviewSerializer(serializers.Serializer):
    due = serializers.BooleanField()
    status = serializers.ChoiceField(
        choices=choices(
            (
                "due",
                "not_due",
                "permission_limited",
                "flag_disabled",
                "no_useful_signal",
                "completed_recently",
            )
        )
    )
    route = serializers.CharField()
    window = DailyQualityWindowSerializer()
    summary = serializers.CharField()
    unresolved_count = serializers.IntegerField(min_value=0)
    completed_count = serializers.IntegerField(min_value=0)
    last_completed_at = serializers.DateTimeField(required=False, allow_null=True)
    action_label = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate(self, attrs):
        route = attrs["route"]
        if (
            not isinstance(route, str)
            or not route.startswith("/")
            or route.startswith("//")
        ):
            raise serializers.ValidationError(
                "Weekly quality review route must be internal."
            )
        if attrs["due"] and attrs["status"] != "due":
            raise serializers.ValidationError(
                "Due weekly quality review must use due status."
            )
        if attrs["status"] == "due" and not attrs["due"]:
            raise serializers.ValidationError(
                "Weekly quality review due status must be marked due."
            )
        return attrs


class DailyQualityStateSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=choices(
            (
                "new_signal",
                "open_action",
                "no_new_signal",
                "permission_limited",
                "unavailable",
            )
        )
    )
    last_reviewed_at = serializers.DateTimeField(required=False, allow_null=True)
    window = DailyQualityWindowSerializer()
    top_signal = DailyQualitySignalSerializer(required=False, allow_null=True)
    primary_action = DailyQualityActionSerializer(required=False, allow_null=True)
    action_cards = DailyQualityActionSerializer(many=True, required=False)
    product_cards = DailyQualityProductCardSerializer(many=True, required=False)
    weekly_review = DailyQualityWeeklyReviewSerializer(
        required=False,
        allow_null=True,
    )
    digest_eligible = serializers.BooleanField()
    digest_suppression_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    diagnostics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate(self, attrs):
        mode = attrs["mode"]
        top_signal = attrs.get("top_signal")
        primary_action = attrs.get("primary_action")
        if mode == "new_signal" and not top_signal:
            raise serializers.ValidationError("Signal modes must include a top_signal.")
        if mode != "unavailable" and not primary_action:
            raise serializers.ValidationError(
                "Renderable daily quality states require a primary_action."
            )
        if not attrs["digest_eligible"] and not attrs.get("digest_suppression_reason"):
            raise serializers.ValidationError(
                "Suppressed daily digest decisions need a reason."
            )
        if attrs["digest_eligible"] and attrs.get("digest_suppression_reason"):
            raise serializers.ValidationError(
                "Eligible daily digest decisions cannot include a suppression reason."
            )
        return attrs


class ActivationPermissionsSerializer(serializers.Serializer):
    role = serializers.CharField(allow_blank=True, allow_null=True)
    can_read = serializers.BooleanField()
    can_write = serializers.BooleanField()
    can_manage_workspace = serializers.BooleanField()
    missing_permissions = serializers.ListField(child=serializers.CharField())
    request_access_href = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    permission_limited = serializers.BooleanField()


class RouteAvailabilityEntrySerializer(serializers.Serializer):
    href = serializers.CharField()
    is_available = serializers.BooleanField()
    reason = serializers.ChoiceField(
        choices=choices(ROUTE_UNAVAILABLE_REASONS),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        if not attrs["is_available"] and not attrs.get("reason"):
            raise serializers.ValidationError(
                "Unavailable routes must include a reason."
            )
        return attrs


class RouteAvailabilitySerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Expected a route availability map.")

        errors = {}
        validated = {}
        for route_key, route_value in data.items():
            serializer = RouteAvailabilityEntrySerializer(data=route_value)
            if serializer.is_valid():
                validated[route_key] = serializer.validated_data
            else:
                errors[route_key] = serializer.errors

        if errors:
            raise serializers.ValidationError(errors)
        return validated

    def to_representation(self, instance):
        return instance


class ActivationEmailContextSerializer(serializers.Serializer):
    campaign_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    email_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    send_log_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    email_status = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    link_issued_at = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    target_stage = serializers.ChoiceField(
        choices=choices(ACTIVATION_STAGES),
        required=False,
        allow_null=True,
    )
    target_event = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    target_route = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    context_status = serializers.ChoiceField(choices=choices(EMAIL_CONTEXT_STATUSES))
    stale_reason = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    resolved_href = serializers.CharField()


class ActivationMeaningfulEventSerializer(serializers.Serializer):
    name = serializers.CharField()
    occurred_at = serializers.DateTimeField()
    is_sample = serializers.BooleanField(default=False)
    path = serializers.ChoiceField(
        choices=choices(PRODUCT_PATHS),
        required=False,
        allow_null=True,
    )
    metadata = serializers.DictField(required=False)


class ActivationDiagnosticsSuppressedActionSerializer(serializers.Serializer):
    id = serializers.CharField()
    reason = serializers.CharField()


class ActivationDiagnosticsSerializer(serializers.Serializer):
    resolver_version = serializers.CharField()
    decision_reason = serializers.CharField()
    matched_rule = serializers.CharField()
    candidate_actions = serializers.ListField(child=serializers.CharField())
    suppressed_actions = ActivationDiagnosticsSuppressedActionSerializer(many=True)
    evaluated_at = serializers.DateTimeField()


class ActivationStateResponseSerializer(serializers.Serializer):
    schema_version = serializers.CharField()
    request_id = serializers.CharField()
    server_time = serializers.DateTimeField()
    workspace_id = serializers.CharField(allow_null=True)
    organization_id = serializers.CharField(allow_null=True)
    user_id = serializers.CharField()
    goal = serializers.ChoiceField(
        choices=choices(ONBOARDING_GOALS),
        allow_null=True,
    )
    persona = serializers.CharField(allow_blank=True, allow_null=True)
    primary_path = serializers.ChoiceField(
        choices=choices(PRODUCT_PATHS),
        allow_null=True,
    )
    stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    stage_copy = ActivationStageCopySerializer(required=False)
    home_mode = serializers.ChoiceField(choices=choices(HOME_MODES))
    is_activated = serializers.BooleanField()
    activated_at = serializers.DateTimeField(allow_null=True)
    recommended_action = ActivationActionSerializer(allow_null=True)
    fallback_action = ActivationActionSerializer()
    progress = ActivationProgressSerializer()
    signals = ActivationSignalsSerializer()
    available_goals = AvailableGoalSerializer(many=True, required=False)
    available_paths = AvailablePathSerializer(many=True)
    journey_plan = ActivationJourneyPlanSerializer(required=False)
    sample_project = SampleProjectStateSerializer()
    prompt = ActivationPromptStateSerializer(required=False, allow_null=True)
    agent = ActivationAgentStateSerializer(required=False, allow_null=True)
    eval = ActivationEvalStateSerializer(required=False, allow_null=True)
    voice = ActivationVoiceStateSerializer(required=False, allow_null=True)
    gateway = ActivationGatewayStateSerializer(required=False, allow_null=True)
    lifecycle = LifecyclePreviewSerializer(required=False, allow_null=True)
    daily_quality = DailyQualityStateSerializer(required=False, allow_null=True)
    email_eligibility = LifecycleEligibilitySerializer()
    permissions = ActivationPermissionsSerializer()
    feature_flags = serializers.DictField(child=serializers.BooleanField())
    route_availability = RouteAvailabilitySerializer()
    email_context = ActivationEmailContextSerializer(allow_null=True)
    last_meaningful_event = ActivationMeaningfulEventSerializer(allow_null=True)
    diagnostics = ActivationDiagnosticsSerializer(allow_null=True)
    warnings = serializers.ListField(child=serializers.CharField())

    def validate_schema_version(self, value):
        if value != ACTIVATION_SCHEMA_VERSION:
            raise serializers.ValidationError("Unsupported activation schema version.")
        return value

    def validate(self, attrs):
        stage = attrs["stage"]
        recommended_action = attrs.get("recommended_action")
        fallback_action = attrs["fallback_action"]

        if stage not in {"workspace_missing"} and recommended_action is None:
            raise serializers.ValidationError(
                "Renderable activation states require a recommended_action."
            )
        if (
            recommended_action
            and recommended_action["id"] == fallback_action["id"]
            and stage != "feature_disabled"
        ):
            raise serializers.ValidationError(
                "Primary and fallback actions must be distinct."
            )

        last_event = attrs.get("last_meaningful_event")
        if attrs["is_activated"] and last_event and last_event.get("is_sample"):
            raise serializers.ValidationError(
                "Sample-only events cannot activate a workspace."
            )

        permissions = attrs["permissions"]
        if (
            recommended_action
            and not permissions["can_write"]
            and recommended_action.get("requires_permission")
            and recommended_action["kind"]
            not in {"request_access", "review", "fallback"}
        ):
            raise serializers.ValidationError(
                "Users without write permission cannot receive write-only CTAs."
            )

        available_hrefs = {
            route["href"] for route in attrs["route_availability"].values()
        }
        if (
            recommended_action
            and recommended_action["route_available"]
            and recommended_action.get("href")
            and recommended_action["href"] not in available_hrefs
        ):
            raise serializers.ValidationError(
                "Recommended action href must appear in route_availability."
            )
        if fallback_action["fallback_href"] not in available_hrefs:
            raise serializers.ValidationError(
                "Fallback action fallback_href must appear in route_availability."
            )

        return attrs


class ActivationStateQuerySerializer(serializers.Serializer):
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    campaign_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    email_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    target_stage = serializers.ChoiceField(
        choices=choices(ACTIVATION_STAGES),
        required=False,
        allow_null=True,
    )
    target_event = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    target_route = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    send_log_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    email_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    status = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    link_issued_at = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    stale_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    context_status = serializers.ChoiceField(
        choices=choices(EMAIL_CONTEXT_STATUSES),
        required=False,
        allow_null=True,
    )
    mode = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    quick_start_goal = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    quick_start_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    quick_start_primary_path = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    debug = serializers.BooleanField(required=False)


class ActivationGoalRequestSerializer(serializers.Serializer):
    goal = serializers.CharField()
    primary_path = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    persona = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    campaign_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    reason = serializers.ChoiceField(
        choices=choices(
            (
                "first_selection",
                "path_change",
                "email_link",
                "manual_switch",
            )
        ),
        required=False,
        allow_null=True,
    )
    expected_stage = serializers.ChoiceField(
        choices=choices(ACTIVATION_STAGES),
        required=False,
        allow_null=True,
    )
    known_goal_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate_goal(self, value):
        canonical = canonical_goal(value)
        if canonical not in ONBOARDING_GOALS:
            raise serializers.ValidationError("Unsupported goal value.")
        return canonical

    def validate_primary_path(self, value):
        if value in {None, ""}:
            return None
        canonical = canonical_path(value)
        if canonical not in PRODUCT_PATHS:
            raise serializers.ValidationError("Unsupported primary path value.")
        return canonical

    def validate(self, attrs):
        primary_path = attrs.get("primary_path")
        if primary_path and primary_path != goal_to_primary_path(attrs["goal"]):
            raise serializers.ValidationError(
                {"primary_path": "Primary path does not match onboarding goal."}
            )
        return attrs


class ActivationGoalResultSerializer(serializers.Serializer):
    goal_id = serializers.CharField()
    activation_state = ActivationStateResponseSerializer()


class ActivationStateApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ActivationStateResponseSerializer()


class ActivationGoalConflictResultSerializer(serializers.Serializer):
    error_code = serializers.ChoiceField(choices=choices(("ONBOARDING_GOAL_CONFLICT",)))
    reason = serializers.CharField()
    current_goal_id = serializers.CharField(allow_null=True)
    activation_state = ActivationStateResponseSerializer()


class ActivationGoalConflictResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    result = ActivationGoalConflictResultSerializer()


class ActivationEventRequestSerializer(serializers.Serializer):
    event_name = serializers.CharField()
    primary_path = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    stage = serializers.ChoiceField(
        choices=choices(ACTIVATION_STAGES),
        required=False,
        allow_null=True,
    )
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    artifact_type = serializers.ChoiceField(
        choices=choices(
            (
                "trace",
                "observe_project",
                "observe_setup",
                "project",
                "agent",
                "graph_execution",
                "test_execution",
                "call_execution",
                "voice_agent",
                "voice_call",
                "voice_scenario",
                "voice_test",
                "dataset",
                "eval",
                "eval_group",
                "eval_run",
                "eval_scorer",
                "eval_task",
                "gateway",
                "gateway_provider",
                "gateway_key",
                "gateway_request",
                "gateway_policy",
                "request_log",
            )
        ),
        required=False,
        allow_null=True,
    )
    artifact_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    project_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    metadata = serializers.DictField(required=False)
    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=160,
    )
    is_sample = serializers.BooleanField(default=False)
    campaign_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    email_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    send_log_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    email_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    target_stage = serializers.ChoiceField(
        choices=choices(ACTIVATION_STAGES),
        required=False,
        allow_null=True,
    )
    target_event = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    link_issued_at = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    stale_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    context_status = serializers.ChoiceField(
        choices=choices(EMAIL_CONTEXT_STATUSES),
        required=False,
        allow_null=True,
    )

    def validate_event_name(self, value):
        canonical = canonical_activation_event(value)
        if canonical not in ONBOARDING_ACTIVATION_EVENTS:
            raise serializers.ValidationError("Unsupported activation event.")
        return canonical

    def validate_primary_path(self, value):
        if value in {None, ""}:
            return None
        canonical = canonical_path(value)
        if canonical not in PRODUCT_PATHS:
            raise serializers.ValidationError("Unsupported primary path value.")
        return canonical

    def validate(self, attrs):
        event_name = attrs["event_name"]
        primary_path = attrs.get("primary_path")
        artifact_type = attrs.get("artifact_type")

        if event_name == "first_quality_loop_completed":
            if primary_path is None:
                raise serializers.ValidationError(
                    {
                        "primary_path": (
                            "First quality loop completion events require "
                            "a primary path."
                        )
                    }
                )
            if primary_path == "observe":
                raise serializers.ValidationError(
                    {
                        "event_name": (
                            "Observe loop completion is recorded from product evidence."
                        )
                    }
                )

        if event_name == "trace_reviewed":
            if primary_path != "observe":
                raise serializers.ValidationError(
                    {"primary_path": "Trace review events must use observe."}
                )
            if artifact_type != "trace":
                raise serializers.ValidationError(
                    {"artifact_type": "Trace review events require trace artifacts."}
                )
            if not attrs.get("artifact_id"):
                raise serializers.ValidationError(
                    {"artifact_id": "Trace review events require artifact_id."}
                )
            if not attrs.get("project_id"):
                raise serializers.ValidationError(
                    {"project_id": "Trace review events require project_id."}
                )
        return attrs


class ActivationEventResultSerializer(serializers.Serializer):
    event_id = serializers.CharField()
    event_name = serializers.ChoiceField(choices=choices(ONBOARDING_ACTIVATION_EVENTS))
    activation_state = ActivationStateResponseSerializer()


class ActivationEventResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ActivationEventResultSerializer()


class OnboardingActivationFactReceiptRequestSerializer(serializers.Serializer):
    type = serializers.CharField(max_length=64)
    export_log_id = serializers.UUIDField()
    idempotency_key = serializers.CharField(max_length=220)
    schema_version = serializers.CharField(max_length=96)
    event_cursor = serializers.CharField(
        max_length=160,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    evaluated_at = serializers.DateTimeField()
    fact = serializers.JSONField()

    def _required_uuid(self, value, path):
        current = value
        for key in path:
            if not isinstance(current, dict):
                raise serializers.ValidationError(
                    {".".join(path): "Must be a valid UUID."}
                )
            current = current.get(key)
        try:
            uuid.UUID(str(current))
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {".".join(path): "Must be a valid UUID."}
            ) from None

    def validate_fact(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Activation fact must be an object.")
        self._required_uuid(value, ("organization", "id"))
        self._required_uuid(value, ("workspace", "id"))
        user = value.get("user") if isinstance(value.get("user"), dict) else {}
        user_id = user.get("id")
        if user_id not in {None, ""}:
            try:
                uuid.UUID(str(user_id))
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    {"user.id": "Must be a valid UUID."}
                ) from None
        return value


class OnboardingActivationFactReceiptResultSerializer(serializers.Serializer):
    receipt_id = serializers.UUIDField()
    created = serializers.BooleanField()
    idempotency_key = serializers.CharField()
    workspace_id = serializers.UUIDField()
    user_id = serializers.UUIDField(required=False, allow_null=True)
    activation_stage = serializers.CharField()
    primary_path = serializers.CharField()
    cohort_keys = serializers.ListField(child=serializers.CharField())


class OnboardingActivationFactReceiptApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = OnboardingActivationFactReceiptResultSerializer()


class SampleProjectRequestSerializer(serializers.Serializer):
    path = serializers.CharField(default="observe")
    manifest_id = serializers.CharField(required=False, allow_blank=True)
    manifest_version = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    open_after_create = serializers.BooleanField(default=False)
    campaign_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    email_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    send_log_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    email_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    target_stage = serializers.ChoiceField(
        choices=choices(ACTIVATION_STAGES),
        required=False,
        allow_null=True,
    )
    target_event = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    link_issued_at = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    stale_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    context_status = serializers.ChoiceField(
        choices=choices(EMAIL_CONTEXT_STATUSES),
        required=False,
        allow_null=True,
    )

    def validate_path(self, value):
        canonical = canonical_path(value)
        if canonical not in PRODUCT_PATHS:
            raise serializers.ValidationError("Unsupported sample project path.")
        if canonical != "observe":
            raise serializers.ValidationError(
                "Only observe sample project is available."
            )
        return canonical


class SampleProjectHideRequestSerializer(serializers.Serializer):
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class SampleProjectResponseSerializer(serializers.Serializer):
    sample_project = SampleProjectStateSerializer()
    activation_state = ActivationStateResponseSerializer()


class SampleProjectApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = SampleProjectResponseSerializer()
