"""
Response serializers for the run_test module — public output contract.

All serializers here are read-only. Their field names and shapes exactly match
what the views currently return, so the frontend is not affected.

Used by:
  - views (via @swagger_auto_schema) for OpenAPI schema documentation
  - views (for wrapping response data in typed serializers)

The actual business logic (to_representation overrides, N+1 optimisations)
stays in RunTestSerializer in simulate.serializers.run_test.
"""

from rest_framework import serializers

from simulate.models import RunTest


class SimulateEvalConfigResponseSerializer(serializers.Serializer):
    """
    Read-only response shape for a SimulateEvalConfig object.
    Mirrors SimulateEvalConfigSimpleSerializer — used for swagger docs and
    response wrapping in AddEvalConfigResponseSerializer.
    """

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True, allow_null=True)
    config = serializers.DictField(read_only=True, allow_null=True)
    mapping = serializers.DictField(read_only=True, allow_null=True)
    filters = serializers.DictField(read_only=True, allow_null=True)
    error_localizer = serializers.BooleanField(read_only=True)
    model = serializers.CharField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True, allow_null=True)
    eval_group = serializers.CharField(read_only=True, allow_null=True)
    template_id = serializers.UUIDField(read_only=True, allow_null=True)


class RunTestResponseSerializer(serializers.ModelSerializer):
    """
    Core read-only response serializer for a RunTest object.
    Field names and order exactly match RunTestSerializer — no frontend breakage.

    This serializer is used as a pure output contract for @swagger_auto_schema
    documentation. The actual serialization (with to_representation logic,
    agent_version snapshot handling, etc.) continues to use RunTestSerializer.
    """

    agent_definition_detail = serializers.DictField(read_only=True, allow_null=True)
    source_type_display = serializers.CharField(read_only=True, allow_null=True)
    scenarios_detail = serializers.ListField(
        child=serializers.DictField(), read_only=True
    )
    simulator_agent_detail = serializers.DictField(read_only=True, allow_null=True)
    simulate_eval_configs_detail = SimulateEvalConfigResponseSerializer(
        many=True, read_only=True
    )
    evals_detail = SimulateEvalConfigResponseSerializer(many=True, read_only=True)
    last_run_at = serializers.DateTimeField(read_only=True, allow_null=True)
    prompt_template_detail = serializers.DictField(read_only=True, allow_null=True)
    prompt_version_detail = serializers.DictField(read_only=True, allow_null=True)
    agent_version = serializers.DictField(read_only=True, allow_null=True)

    class Meta:
        model = RunTest
        fields = [
            "id",
            "name",
            "description",
            "agent_definition",
            "agent_version",
            "agent_definition_detail",
            "source_type",
            "source_type_display",
            "prompt_template",
            "prompt_template_detail",
            "prompt_version",
            "prompt_version_detail",
            "scenarios",
            "scenarios_detail",
            "dataset_row_ids",
            "simulator_agent",
            "simulator_agent_detail",
            "simulate_eval_configs",
            "simulate_eval_configs_detail",
            "evals_detail",
            "organization",
            "enable_tool_evaluation",
            "created_at",
            "updated_at",
            "last_run_at",
            "deleted",
            "deleted_at",
        ]
        read_only_fields = fields


class AddEvalConfigResponseSerializer(serializers.Serializer):
    """
    Response for POST /run-tests/{run_test_id}/eval-configs/ — HTTP 201.
    Shape: {"message": "...", "created_eval_configs": [...], "run_test_id": "...", "errors": [...]}
    """

    message = serializers.CharField(read_only=True)
    created_eval_configs = SimulateEvalConfigResponseSerializer(
        many=True, read_only=True
    )
    run_test_id = serializers.UUIDField(read_only=True)
    errors = serializers.ListField(
        child=serializers.CharField(), read_only=True, required=False
    )


class TestExecutionItemResponseSerializer(serializers.Serializer):
    """
    Response shape for a single item in GET /run-tests/{run_test_id}/executions/.
    Exactly matches the dict built in RunTestExecutionsView.get().
    """

    id = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    scenarios = serializers.CharField(read_only=True)
    start_time = serializers.CharField(read_only=True, allow_null=True)
    duration = serializers.IntegerField(read_only=True)
    error_reason = serializers.CharField(read_only=True, allow_null=True)
    success_rate = serializers.FloatField(read_only=True)
    avg_response_time = serializers.FloatField(read_only=True)
    calls = serializers.IntegerField(read_only=True)
    calls_attempted = serializers.IntegerField(read_only=True)
    connected_calls = serializers.IntegerField(read_only=True)
    agent_version = serializers.CharField(read_only=True)
    agent_definition = serializers.CharField(read_only=True)
    calls_connected_percentage = serializers.FloatField(read_only=True)
    total_chats = serializers.IntegerField(read_only=True)
    agent_type = serializers.CharField(read_only=True)
    total_number_of_fagi_agent_turns = serializers.IntegerField(read_only=True)
    source_type = serializers.CharField(read_only=True)


# Kept for backward compatibility — the swagger decorator references this name.
# The actual per-item shape is TestExecutionItemResponseSerializer above.
RunTestExecutionsResponseSerializer = TestExecutionItemResponseSerializer


class RunTestScenarioItemResponseSerializer(serializers.Serializer):
    """
    Response shape for a single item in GET /run-tests/{run_test_id}/scenarios/.
    Shape: {"id": "...", "name": "...", "row_count": 0}
    """

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    row_count = serializers.IntegerField(read_only=True)


class RunTestMessageResponseSerializer(serializers.Serializer):
    """
    Simple message response — used for soft-delete (HTTP 200).
    Shape: {"message": "..."}
    """

    message = serializers.CharField(read_only=True)


class RunTestErrorResponseSerializer(serializers.Serializer):
    """
    Standard error response shape for all run-test endpoints.
    Used for @swagger_auto_schema documentation only — not applied to actual responses.
    Shape: {"error": "...", "details": {...}}  — details only present on HTTP 400.
    """

    error = serializers.CharField(read_only=True)
    details = serializers.DictField(required=False, read_only=True)
