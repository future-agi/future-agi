import re

from rest_framework import serializers

from simulate.serializers.harness_job import RUNNER_RESERVED_ENVIRONMENT


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

    environment_values = serializers.DictField(
        child=serializers.CharField(
            max_length=65536, allow_blank=True, trim_whitespace=False
        ),
        required=False,
        default=dict,
        write_only=True,
        help_text=(
            "Fresh job-scoped environment for a repository-backed harness rerun. "
            "Values are forwarded to the sandbox and are not persisted."
        ),
    )

    def validate_environment_values(self, value):
        invalid = [
            str(name)
            for name, item in value.items()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name))
            or "\x00" in item
            or str(name) in RUNNER_RESERVED_ENVIRONMENT
            or str(name).startswith("ALK_")
        ]
        if invalid:
            raise serializers.ValidationError(
                f"invalid environment variable names or values: {', '.join(invalid)}"
            )
        if len(value) > 256 or sum(
            len(str(name).encode()) + len(item.encode())
            for name, item in value.items()
        ) > 262_144:
            raise serializers.ValidationError(
                "uploaded rerun environment exceeds the 256 variable / 256 KiB limit"
            )
        return value

    def validate(self, data):
        """Validate that either call_execution_ids or select_all is provided"""
        if not data.get("select_all") and not data.get("call_execution_ids"):
            raise serializers.ValidationError(
                "Either 'select_all' must be True or 'call_execution_ids' must be provided"
            )
        return data
