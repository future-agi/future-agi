from rest_framework import serializers

from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace_filter,
)
from simulate.models import (
    AgentPromptOptimiserRun,
    AgentPromptOptimiserRunStep,
    ComponentEvaluation,
    PromptTrial,
    TestExecution,
    TrialItemResult,
)
from simulate.utils.llm import get_api_key_for_model

# Optional keys accepted by all optimizer types
COMMON_OPTIONAL_CONFIG_KEYS = {"task_description"}

OPTIMISER_REQUIRED_CONFIG_KEYS = {
    "random_search": ["num_variations"],
    "gepa": ["max_metric_calls"],
    "protegi": [
        "beam_size",
        "num_gradients",
        "errors_per_gradient",
        "prompts_per_gradient",
        "num_rounds",
    ],
    "bayesian": ["min_examples", "max_examples", "n_trials"],
    "metaprompt": ["task_description", "num_rounds"],
    "promptwizard": ["mutate_rounds", "refine_iterations", "beam_size"],
}


class AgentPromptOptimiserRunCreateSerializer(serializers.ModelSerializer):
    test_execution_id = serializers.UUIDField(write_only=True, required=True)
    optimiser_type = serializers.ChoiceField(
        choices=AgentPromptOptimiserRun.OptimiserType.choices, required=True
    )

    class Meta:
        model = AgentPromptOptimiserRun
        fields = (
            "id",
            "name",
            "test_execution_id",
            "optimiser_type",
            "model",
            "configuration",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def _test_execution_queryset(self):
        request = self.context.get("request")
        queryset = TestExecution.objects.select_related(
            "agent_optimiser",
            "run_test__organization",
            "run_test__workspace",
        )
        organization = request_organization(request)
        if organization is not None:
            queryset = queryset.filter(run_test__organization=organization)
        return queryset.filter(
            request_workspace_filter(request, field_name="run_test__workspace")
        )

    def validate_test_execution_id(self, value):
        if not self._test_execution_queryset().filter(id=value).exists():
            raise serializers.ValidationError(
                "TestExecution with this ID does not exist."
            )
        return value

    def validate_configuration(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("This field must be a valid JSON object.")

        optimiser_type = self.initial_data.get("optimiser_type")
        required_keys = OPTIMISER_REQUIRED_CONFIG_KEYS.get(optimiser_type)

        if required_keys:
            required_keys_set = set(required_keys)
            provided_keys_set = set(value.keys())

            # Check for missing keys
            missing_keys = required_keys_set - provided_keys_set
            if missing_keys:
                raise serializers.ValidationError(
                    f"Missing required keys for optimizer '{optimiser_type}': "
                    f"{', '.join(missing_keys)}"
                )

            # Check for extra keys
            extra_keys = (
                provided_keys_set - required_keys_set - COMMON_OPTIONAL_CONFIG_KEYS
            )
            if extra_keys:
                raise serializers.ValidationError(
                    f"Unexpected keys provided for optimizer '{optimiser_type}': "
                    f"{', '.join(extra_keys)}"
                )

        return value

    def create(self, validated_data):
        test_execution = self._test_execution_queryset().get(
            id=validated_data.pop("test_execution_id")
        )
        agent_optimiser = test_execution.agent_optimiser

        if not agent_optimiser:
            raise serializers.ValidationError(
                "The provided TestExecution does not have an associated AgentOptimiser."
            )

        agent_optimiser_run = agent_optimiser.latest_run

        if not agent_optimiser_run:
            raise serializers.ValidationError(
                "No AgentOptimiserRun found for the associated AgentOptimiser."
            )

        # Validate that the user has configured an API key for the selected model
        organization = test_execution.run_test.organization
        workspace = test_execution.run_test.workspace
        workspace_id = workspace.id if workspace else None
        model_name = validated_data.get("model")
        try:
            get_api_key_for_model(
                model_name=model_name,
                organization_id=organization.id,
                workspace_id=workspace_id,
            )
        except ValueError as e:
            raise serializers.ValidationError(str(e)) from e

        validated_data["test_execution"] = test_execution
        validated_data["agent_optimiser"] = agent_optimiser
        validated_data["agent_optimiser_run"] = agent_optimiser_run

        return super().create(validated_data)


class AgentPromptOptimiserRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentPromptOptimiserRun
        fields = [
            "id",
            "agent_optimiser",
            "agent_optimiser_run",
            "test_execution",
            "optimiser_type",
            "model",
            "status",
            "result",
            "configuration",
        ]


class AgentPromptOptimiserRunListSerializer(serializers.ModelSerializer):
    """Serializer for listing agent prompt optimiser runs in table format."""

    optimisation_name = serializers.CharField(source="name")
    started_at = serializers.DateTimeField(source="created_at")
    no_of_trials = serializers.SerializerMethodField()

    class Meta:
        model = AgentPromptOptimiserRun
        fields = [
            "id",
            "optimisation_name",
            "started_at",
            "no_of_trials",
            "optimiser_type",
            "status",
            "error_message",
            "configuration",
            "model",
        ]

    def get_no_of_trials(self, obj):
        return obj.trials.count()


class AgentPromptOptimiserRunStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentPromptOptimiserRunStep
        fields = (
            "id",
            "name",
            "description",
            "status",
            "metadata",
            "step_number",
            "created_at",
            "updated_at",
        )

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.status == AgentPromptOptimiserRunStep.Status.PENDING:
            representation["created_at"] = None
            representation["updated_at"] = None
        return representation


class ComponentEvaluationSerializer(serializers.ModelSerializer):
    """Serializer for individual evaluation component scores."""

    eval_config_id = serializers.UUIDField(source="eval_config.id", read_only=True)
    eval_name = serializers.CharField(source="eval_config.name", read_only=True)

    class Meta:
        model = ComponentEvaluation
        fields = (
            "id",
            "eval_config_id",
            "eval_name",
            "score",
            "reason",
            "created_at",
        )


class TrialItemResultSerializer(serializers.ModelSerializer):
    """Serializer for individual CallExecution results within a trial."""

    component_evaluations = ComponentEvaluationSerializer(many=True, read_only=True)
    call_execution_id = serializers.UUIDField(
        source="call_execution.id", read_only=True
    )

    class Meta:
        model = TrialItemResult
        fields = (
            "id",
            "call_execution_id",
            "score",
            "reason",
            "input_text",
            "output_text",
            "metadata",
            "component_evaluations",
            "created_at",
        )


class TrialItemResultListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing trial items without nested component evals."""

    call_execution_id = serializers.UUIDField(
        source="call_execution.id", read_only=True
    )

    class Meta:
        model = TrialItemResult
        fields = (
            "id",
            "call_execution_id",
            "score",
            "reason",
            "created_at",
        )


class PromptTrialSerializer(serializers.ModelSerializer):
    """Serializer for prompt trials with nested trial item results."""

    trial_items = TrialItemResultListSerializer(many=True, read_only=True)

    class Meta:
        model = PromptTrial
        fields = (
            "id",
            "trial_number",
            "is_baseline",
            "prompt",
            "average_score",
            "metadata",
            "trial_items",
            "created_at",
        )


class PromptTrialListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing trials (e.g., for graphs)."""

    class Meta:
        model = PromptTrial
        fields = (
            "id",
            "trial_number",
            "is_baseline",
            "average_score",
            "created_at",
        )


class AgentPromptOptimiserColumnConfigSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    is_visible = serializers.BooleanField(read_only=True)


class AgentPromptOptimiserConfigurationSerializer(serializers.Serializer):
    """Known optimiser configuration fields accepted across optimiser types."""

    num_gradients = serializers.IntegerField(required=False)
    errors_per_gradient = serializers.IntegerField(required=False)
    prompts_per_gradient = serializers.IntegerField(required=False)
    beam_size = serializers.IntegerField(required=False)
    num_rounds = serializers.IntegerField(required=False)
    num_variations = serializers.IntegerField(required=False)
    max_metric_calls = serializers.IntegerField(required=False)
    min_examples = serializers.IntegerField(required=False)
    max_examples = serializers.IntegerField(required=False)
    n_trials = serializers.IntegerField(required=False)
    task_description = serializers.CharField(required=False, allow_blank=True)
    mutate_rounds = serializers.IntegerField(required=False)
    refine_iterations = serializers.IntegerField(required=False)


class AgentPromptOptimiserParameterSerializer(serializers.Serializer):
    key = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    value = serializers.JSONField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)


class AgentPromptOptimiserTrialEvalScoreSerializer(serializers.Serializer):
    score = serializers.FloatField(read_only=True, allow_null=True)
    percentage_change = serializers.FloatField(read_only=True, allow_null=True)


class AgentPromptOptimiserTrialTableRowSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    trial = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True, allow_null=True, required=False)
    score_percentage_change = serializers.FloatField(
        read_only=True, allow_null=True, required=False
    )
    prompt = serializers.CharField(read_only=True, allow_blank=True, allow_null=True)
    is_best = serializers.BooleanField(read_only=True, required=False)
    eval_scores = serializers.DictField(
        child=AgentPromptOptimiserTrialEvalScoreSerializer(),
        read_only=True,
        required=False,
        help_text="Dynamic eval-config UUID keys are returned as top-level row keys at runtime.",
    )


class AgentPromptOptimiserRunDetailResultSerializer(serializers.Serializer):
    optimiser_name = serializers.CharField(read_only=True)
    optimiser_type = serializers.CharField(read_only=True)
    model = serializers.CharField(read_only=True)
    provider_logo = serializers.CharField(read_only=True, allow_null=True)
    configuration = AgentPromptOptimiserConfigurationSerializer(
        read_only=True, allow_null=True
    )
    parameters = AgentPromptOptimiserParameterSerializer(many=True, read_only=True)
    start_time = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)
    table = AgentPromptOptimiserTrialTableRowSerializer(many=True, read_only=True)
    column_config = AgentPromptOptimiserColumnConfigSerializer(
        many=True, read_only=True
    )


class AgentPromptOptimiserRunDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AgentPromptOptimiserRunDetailResultSerializer()


class AgentPromptOptimiserRunStepsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AgentPromptOptimiserRunStepSerializer(many=True)


class AgentPromptOptimiserGraphEvaluationSerializer(serializers.Serializer):
    trial_id = serializers.UUIDField(read_only=True)
    trial_number = serializers.IntegerField(read_only=True)
    trial_name = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True, allow_null=True)


class AgentPromptOptimiserGraphSeriesSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    evaluations = AgentPromptOptimiserGraphEvaluationSerializer(
        many=True, read_only=True
    )


class AgentPromptOptimiserGraphResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.DictField(
        child=AgentPromptOptimiserGraphSeriesSerializer(),
        help_text="Dictionary keyed by eval-config UUID.",
    )


class AgentPromptOptimiserTrialHeaderSerializer(serializers.Serializer):
    trial_name = serializers.CharField(read_only=True)
    optimisation_name = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    score = serializers.FloatField(read_only=True, allow_null=True)
    score_percentage_change = serializers.FloatField(read_only=True, allow_null=True)


class AgentPromptOptimiserTrialPromptResultSerializer(
    AgentPromptOptimiserTrialHeaderSerializer
):
    trial_prompt = serializers.CharField(read_only=True, allow_null=True)
    base_prompt = serializers.CharField(read_only=True, allow_null=True)


class AgentPromptOptimiserTrialPromptResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AgentPromptOptimiserTrialPromptResultSerializer()


class AgentPromptOptimiserTrialEvaluationRowSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    eval_name = serializers.CharField(read_only=True, allow_null=True)
    eval_template_description = serializers.CharField(
        read_only=True, allow_null=True, allow_blank=True
    )
    score = serializers.FloatField(read_only=True, allow_null=True)
    score_percentage_change = serializers.FloatField(read_only=True, allow_null=True)


class AgentPromptOptimiserTrialEvaluationsResultSerializer(
    AgentPromptOptimiserTrialHeaderSerializer
):
    table = AgentPromptOptimiserTrialEvaluationRowSerializer(many=True, read_only=True)
    column_config = AgentPromptOptimiserColumnConfigSerializer(
        many=True, read_only=True
    )


class AgentPromptOptimiserTrialEvaluationsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AgentPromptOptimiserTrialEvaluationsResultSerializer()


class AgentPromptOptimiserTrialScenarioRowSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    input_text = serializers.CharField(read_only=True, allow_blank=True)
    output_text = serializers.CharField(read_only=True, allow_blank=True)
    eval_scores = serializers.DictField(
        child=serializers.FloatField(allow_null=True),
        read_only=True,
        required=False,
        help_text="Dynamic eval-config UUID keys are returned as top-level row keys at runtime.",
    )


class AgentPromptOptimiserTrialScenariosResultSerializer(
    AgentPromptOptimiserTrialHeaderSerializer
):
    table = AgentPromptOptimiserTrialScenarioRowSerializer(many=True, read_only=True)
    column_config = AgentPromptOptimiserColumnConfigSerializer(
        many=True, read_only=True
    )


class AgentPromptOptimiserTrialScenariosResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AgentPromptOptimiserTrialScenariosResultSerializer()


class AgentPromptOptimiserComponentEvalResultSerializer(serializers.Serializer):
    score = serializers.FloatField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class AgentPromptOptimiserIndividualResultMetadataSerializer(serializers.Serializer):
    input = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    output = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    component_evals = serializers.DictField(
        child=AgentPromptOptimiserComponentEvalResultSerializer(), required=False
    )


class AgentPromptOptimiserIndividualResultSerializer(serializers.Serializer):
    score = serializers.FloatField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    metadata = AgentPromptOptimiserIndividualResultMetadataSerializer(required=False)


class AgentPromptOptimiserRawTrialResultSerializer(serializers.Serializer):
    prompt = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    average_score = serializers.FloatField(required=False, allow_null=True)
    is_baseline = serializers.BooleanField(required=False)
    individual_results = serializers.DictField(
        child=AgentPromptOptimiserIndividualResultSerializer(), required=False
    )


class AgentPromptOptimiserRawResultSerializer(serializers.Serializer):
    history = AgentPromptOptimiserRawTrialResultSerializer(many=True, required=False)
    best_prompt = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    final_score = serializers.FloatField(required=False, allow_null=True)
    best_score = serializers.FloatField(required=False, allow_null=True)
    trials_run = serializers.IntegerField(required=False)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class AgentPromptOptimiserRunModelResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    agent_optimiser = serializers.UUIDField(read_only=True)
    agent_optimiser_run = serializers.UUIDField(read_only=True)
    test_execution = serializers.UUIDField(read_only=True)
    optimiser_type = serializers.ChoiceField(
        choices=AgentPromptOptimiserRun.OptimiserType.choices, read_only=True
    )
    model = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(
        choices=AgentPromptOptimiserRun.Status.choices, read_only=True
    )
    result = AgentPromptOptimiserRawResultSerializer(
        read_only=True, allow_null=True, required=False
    )
    configuration = AgentPromptOptimiserConfigurationSerializer(
        read_only=True, allow_null=True
    )
