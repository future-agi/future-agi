from rest_framework import serializers

from accounts.services.onboarding.constants import (
    ACTION_KINDS,
    ACTIVATION_SCHEMA_VERSION,
    ACTIVATION_STAGES,
    AVAILABLE_PATH_STATUSES,
    EMAIL_CONTEXT_STATUSES,
    HOME_MODES,
    LIFECYCLE_SUPPRESSION_REASONS,
    ONBOARDING_GOALS,
    PRODUCT_PATHS,
    PROGRESS_STATES,
    ROUTE_UNAVAILABLE_REASONS,
    SAMPLE_PROJECT_STATUSES,
    canonical_goal,
    canonical_path,
    choices,
)
from accounts.services.onboarding.goals import goal_to_primary_path


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


class ActivationSignalsSerializer(serializers.Serializer):
    provider_keys = serializers.IntegerField(min_value=0, default=0)
    datasets = serializers.IntegerField(min_value=0, default=0)
    evals = serializers.IntegerField(min_value=0, default=0)
    eval_runs = serializers.IntegerField(min_value=0, default=0)
    prompt_templates = serializers.IntegerField(min_value=0, default=0)
    prompt_versions = serializers.IntegerField(min_value=0, default=0)
    prompt_comparisons = serializers.IntegerField(min_value=0, default=0)
    agents = serializers.IntegerField(min_value=0, default=0)
    agent_prototype_runs = serializers.IntegerField(min_value=0, default=0)
    observe_projects = serializers.IntegerField(min_value=0, default=0)
    traces = serializers.IntegerField(min_value=0, default=0)
    trace_reviews = serializers.IntegerField(min_value=0, default=0)
    gateway_keys = serializers.IntegerField(min_value=0, default=0)
    gateway_requests = serializers.IntegerField(min_value=0, default=0)
    gateway_policies = serializers.IntegerField(min_value=0, default=0)
    voice_agents = serializers.IntegerField(min_value=0, default=0)
    voice_simulations = serializers.IntegerField(min_value=0, default=0)
    voice_calls = serializers.IntegerField(min_value=0, default=0)
    voice_reviews = serializers.IntegerField(min_value=0, default=0)
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


class SampleProjectStateSerializer(serializers.Serializer):
    available = serializers.BooleanField()
    created = serializers.BooleanField()
    status = serializers.ChoiceField(choices=choices(SAMPLE_PROJECT_STATUSES))
    href = serializers.CharField(allow_blank=True, allow_null=True)
    version = serializers.CharField(allow_blank=True, allow_null=True)
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
            "stale_manifest",
            "repair_required",
        }:
            raise serializers.ValidationError(
                "Created samples must be ready, partial, stale, or repairable."
            )
        if status == "partial" and not attrs["missing_artifacts"]:
            raise serializers.ValidationError(
                "Partial samples must list missing_artifacts."
            )
        if status == "ready" and not attrs["entry_routes"]:
            raise serializers.ValidationError("Ready samples must list entry_routes.")
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
    campaign_key = serializers.CharField()
    email_key = serializers.CharField()
    target_stage = serializers.ChoiceField(choices=choices(ACTIVATION_STAGES))
    target_event = serializers.CharField()
    target_route = serializers.CharField()
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
    home_mode = serializers.ChoiceField(choices=choices(HOME_MODES))
    is_activated = serializers.BooleanField()
    activated_at = serializers.DateTimeField(allow_null=True)
    recommended_action = ActivationActionSerializer(allow_null=True)
    fallback_action = ActivationActionSerializer()
    progress = ActivationProgressSerializer()
    signals = ActivationSignalsSerializer()
    available_paths = AvailablePathSerializer(many=True)
    sample_project = SampleProjectStateSerializer()
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


class SampleProjectRequestSerializer(serializers.Serializer):
    path = serializers.CharField()
    manifest_id = serializers.CharField()
    manifest_version = serializers.CharField()
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    open_after_create = serializers.BooleanField(default=False)

    def validate_path(self, value):
        canonical = canonical_path(value)
        if canonical not in PRODUCT_PATHS:
            raise serializers.ValidationError("Unsupported sample project path.")
        return canonical


class SampleProjectResponseSerializer(serializers.Serializer):
    sample_project = SampleProjectStateSerializer()
    activation_state = ActivationStateResponseSerializer()
