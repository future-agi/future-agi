from rest_framework import serializers


class TestExecutionCancelSerializer(serializers.Serializer):
    """Used by the alternative /run-tests/{run_test_id}/cancel/ route only.
    The public POST /test-executions/{test_execution_id}/cancel/ takes no body."""

    run_test_id = serializers.UUIDField(required=False)


class CallExecutionRerunSerializer(serializers.Serializer):
    """Serializer for call execution rerun requests"""

    rerun_type = serializers.ChoiceField(
        choices=[
            ("eval_only", "Evaluation Only"),
            ("call_and_eval", "Call and Evaluation"),
        ],
        help_text="Type of rerun: evaluation only or call plus evaluation",
    )

    call_execution_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of specific call execution IDs to rerun",
    )

    select_all = serializers.BooleanField(
        default=False,
        help_text="Whether to rerun all call executions in the test execution",
    )

    def validate(self, data):
        """Validate that either call_execution_ids or select_all is provided"""
        if not data.get("select_all") and not data.get("call_execution_ids"):
            raise serializers.ValidationError(
                "Either 'select_all' must be True or 'call_execution_ids' must be provided"
            )
        return data
