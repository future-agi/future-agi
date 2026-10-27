from rest_framework import serializers

from simulate.serializers.response.run_test import RunTestResponseSerializer


class PromptSimulationTemplateSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class PromptSimulationListResultSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
    page = serializers.IntegerField(read_only=True)
    limit = serializers.IntegerField(read_only=True)
    results = RunTestResponseSerializer(many=True, read_only=True)
    prompt_template = PromptSimulationTemplateSummarySerializer(read_only=True)


class PromptSimulationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = PromptSimulationListResultSerializer()


class PromptSimulationRunResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = RunTestResponseSerializer()


class PromptSimulationUpdateRequestSerializer(serializers.Serializer):
    prompt_version_id = serializers.CharField(max_length=255, required=False)
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False
    )
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    enable_tool_evaluation = serializers.BooleanField(required=False)


class ExecutePromptSimulationRequestSerializer(serializers.Serializer):
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False, default=list
    )
    select_all = serializers.BooleanField(required=False, default=False)


class ExecutePromptSimulationResultSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)
    execution_id = serializers.UUIDField(read_only=True)
    run_test_id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    total_scenarios = serializers.IntegerField(read_only=True)
    total_calls = serializers.IntegerField(read_only=True)
    scenario_ids = serializers.ListField(child=serializers.UUIDField())


class ExecutePromptSimulationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ExecutePromptSimulationResultSerializer()


class PromptSimulationScenarioItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    scenario_type = serializers.CharField(read_only=True)
    dataset_id = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class PromptSimulationScenariosResultSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
    page = serializers.IntegerField(read_only=True)
    limit = serializers.IntegerField(read_only=True)
    results = PromptSimulationScenarioItemSerializer(many=True, read_only=True)


class PromptSimulationScenariosResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = PromptSimulationScenariosResultSerializer()
