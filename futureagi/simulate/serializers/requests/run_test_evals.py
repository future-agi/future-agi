import json

from rest_framework import serializers


class EvalConfigDefinitionSerializer(serializers.Serializer):
    """Defines a single evaluation configuration item within AddEvalConfigsRequestSerializer."""

    template_id = serializers.UUIDField(
        required=True,
        help_text="UUID of the evaluation template to use.",
    )
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Name for this evaluation configuration. Defaults to 'Eval-<template_id>' if omitted.",
    )
    config = serializers.DictField(
        required=False,
        default=dict,
        help_text="Template-specific configuration parameters.",
    )
    mapping = serializers.DictField(
        required=False,
        default=dict,
        help_text="Maps test execution data fields to the evaluation template's expected inputs.",
    )
    filters = serializers.DictField(
        required=False,
        default=dict,
        help_text="Filter criteria to restrict which test results are evaluated.",
    )
    error_localizer = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Enables granular error localization on evaluation failures.",
    )
    model = serializers.CharField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Model to use for running this evaluation.",
    )


class AddEvalConfigsRequestSerializer(serializers.Serializer):
    """Request serializer for POST /simulate/run-tests/{run_test_id}/eval-configs/"""

    evaluations_config = serializers.ListField(
        child=EvalConfigDefinitionSerializer(),
        required=True,
        min_length=1,
        help_text="Array of evaluation configuration objects to add. At least one required.",
    )

    def validate_evaluations_config(self, value):
        """Check for duplicate names within the request."""
        names_seen = []
        for item in value:
            name = item.get("name")
            if not name:
                # Default name will be assigned in the view; skip duplicate check
                continue
            if name in names_seen:
                raise serializers.ValidationError(
                    f"Duplicate eval name '{name}' found in the request. "
                    "Each evaluation config must have a unique name."
                )
            names_seen.append(name)
        return value


class EvalConfigUpdateRequestSerializer(serializers.Serializer):
    """Request serializer for POST /simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/update/"""

    config = serializers.DictField(
        required=False,
        allow_null=True,
        help_text="Updated evaluation configuration parameters.",
    )
    mapping = serializers.DictField(
        required=False,
        allow_null=True,
        help_text="Updated field mapping between test data and evaluation inputs.",
    )
    model = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Model to use for evaluations.",
    )
    error_localizer = serializers.BooleanField(
        required=False,
        help_text="Enable granular error localization in evaluation results.",
    )
    kb_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="UUID of a knowledge base to use for grounding. Pass null to clear.",
    )
    name = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Updated name for the evaluation configuration.",
    )
    run = serializers.BooleanField(
        required=False,
        default=False,
        help_text="When true, triggers an immediate rerun after updating. Defaults to false.",
    )
    test_execution_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="UUID of the test execution to rerun against. Required when run is true.",
    )

    def validate(self, data):
        """Ensure test_execution_id is provided when run=True."""
        if data.get("run") and not data.get("test_execution_id"):
            raise serializers.ValidationError(
                {"test_execution_id": "test_execution_id is required when run is true"}
            )
        return data


class EvalSummaryFilterSerializer(serializers.Serializer):
    """Query parameter serializer for GET /simulate/run-tests/{run_test_id}/eval-summary/"""

    execution_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="UUID of a specific test execution to scope the summary to. "
        "If omitted, aggregates across all executions.",
    )


class EvalSummaryComparisonFilterSerializer(serializers.Serializer):
    """Query parameter serializer for GET /simulate/run-tests/{run_test_id}/eval-summary-comparison/"""

    execution_ids = serializers.CharField(
        required=True,
        help_text="JSON-encoded array of test execution UUIDs to compare. "
        'Example: ["uuid1","uuid2"]. Must be URL-encoded.',
    )

    def validate_execution_ids(self, value):
        """Parse JSON string and validate the resulting list is non-empty."""
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            raise serializers.ValidationError("execution_ids must be valid JSON")
        if not isinstance(parsed, list):
            raise serializers.ValidationError("execution_ids must be a JSON array")
        if not parsed:
            raise serializers.ValidationError("execution_ids list is required")
        return parsed


class RunNewEvalsOnTestExecutionSerializer(serializers.Serializer):
    """Serializer for running new evaluations on existing test executions.

    Migrated from simulate/serializers/test_execution.py.
    """

    test_execution_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of specific test execution IDs to run evaluations on",
    )

    select_all = serializers.BooleanField(
        default=False,
        help_text="Whether to run evaluations on all test executions in the run test",
    )

    eval_config_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        help_text="List of SimulateEvalConfig IDs to run on the test executions",
    )

    enable_tool_evaluation = serializers.BooleanField(
        required=False,
        default=None,
        help_text="Whether to enable tool evaluation for this run "
        "(if not provided, uses the run test's current setting)",
    )

    def validate(self, data):
        """Validate that either test_execution_ids or select_all is provided, and eval_config_ids is not empty."""
        if not data.get("select_all") and not data.get("test_execution_ids"):
            raise serializers.ValidationError(
                "Either 'select_all' must be True or 'test_execution_ids' must be provided"
            )
        if not data.get("eval_config_ids"):
            raise serializers.ValidationError("eval_config_ids cannot be empty")
        return data
