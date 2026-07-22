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
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tracer.serializers.filters import filter_list_field, json_object_field


class SimulateEvalConfigResponseSerializer(serializers.Serializer):
    """
    Read-only response shape for a SimulateEvalConfig object.
    Mirrors SimulateEvalConfigSimpleSerializer — used for swagger docs and
    response wrapping in AddEvalConfigResponseSerializer.
    """

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True, allow_null=True)
    config = json_object_field(read_only=True, allow_null=True)
    mapping = json_object_field(read_only=True, allow_null=True)
    filters = filter_list_field(read_only=True, default=list)
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

    description = serializers.CharField(
        read_only=True, allow_null=True, allow_blank=True
    )
    agent_definition_detail = serializers.JSONField(read_only=True, allow_null=True)
    source_type_display = serializers.CharField(read_only=True, allow_null=True)
    scenarios_detail = serializers.ListField(
        child=serializers.JSONField(), read_only=True
    )
    simulator_agent_detail = serializers.JSONField(read_only=True, allow_null=True)
    simulate_eval_configs_detail = SimulateEvalConfigResponseSerializer(
        many=True, read_only=True
    )
    evals_detail = SimulateEvalConfigResponseSerializer(many=True, read_only=True)
    last_run_at = serializers.DateTimeField(read_only=True, allow_null=True)
    prompt_template_detail = serializers.JSONField(read_only=True, allow_null=True)
    prompt_version_detail = serializers.JSONField(read_only=True, allow_null=True)
    agent_version = serializers.JSONField(read_only=True, allow_null=True)

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


class RunTestExecutionsResponseSerializer(serializers.Serializer):
    """Paginated envelope returned by GET /run-tests/{run_test_id}/executions/.

    Runtime shape comes from ``paginator.get_paginated_response(...)``:
    ``{count, next, previous, results: [TestExecutionItem, ...]}``.
    """

    count = serializers.IntegerField(read_only=True)
    next = serializers.CharField(read_only=True, allow_null=True)
    previous = serializers.CharField(read_only=True, allow_null=True)
    results = TestExecutionItemResponseSerializer(many=True, read_only=True)


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


class RunTestExecutionResponseSerializer(serializers.Serializer):
    """Response for POST /run-tests/{run_test_id}/execute/."""

    message = serializers.CharField(read_only=True)
    execution_id = serializers.UUIDField(read_only=True)
    run_test_id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    total_scenarios = serializers.IntegerField(read_only=True)
    total_calls = serializers.IntegerField(read_only=True)
    scenario_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True)


class RunTestCallExecutionsResponseSerializer(serializers.Serializer):
    """Paginated response for call executions attached to a run test."""

    count = serializers.IntegerField(read_only=True)
    next = serializers.CharField(read_only=True, allow_null=True)
    previous = serializers.CharField(read_only=True, allow_null=True)
    results = serializers.ListField(child=serializers.DictField(), read_only=True)
    total_pages = serializers.IntegerField(read_only=True)
    current_page = serializers.IntegerField(read_only=True)


class RunTestErrorResponseSerializer(ApiTextErrorResponseSerializer):
    """
    Standard error response shape for all run-test endpoints.
    """
