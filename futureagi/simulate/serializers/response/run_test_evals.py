from rest_framework import serializers

from simulate.serializers.run_test import SimulateEvalConfigSimpleSerializer


class EvalConfigResponseSerializer(SimulateEvalConfigSimpleSerializer):
    """Response shape for a single SimulateEvalConfig object.

    Inherits from SimulateEvalConfigSimpleSerializer so it correctly serializes
    model instances (e.g., template_id sourced from eval_template FK,
    eval_group resolved via get_eval_group()).
    """


class AddEvalConfigsResponseSerializer(serializers.Serializer):
    """Response serializer for POST /simulate/run-tests/{run_test_id}/eval-configs/  (HTTP 201)"""

    message = serializers.CharField()
    created_eval_configs = EvalConfigResponseSerializer(many=True)
    run_test_id = serializers.UUIDField()
    warnings = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Non-fatal issues encountered while processing individual configs.",
    )


class EvalConfigUpdateResponseSerializer(serializers.Serializer):
    """Response serializer for POST /simulate/run-tests/{run_test_id}/eval-configs/{id}/update/  (HTTP 200)

    When run=False: message + eval_config_id + run_test_id.
    When run=True: additionally includes test_execution_id, call_execution_count, note.
    """

    message = serializers.CharField()
    eval_config_id = serializers.UUIDField()
    run_test_id = serializers.UUIDField()
    test_execution_id = serializers.UUIDField(required=False, allow_null=True)
    call_execution_count = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_null=True)


class DeleteEvalConfigResponseSerializer(serializers.Serializer):
    """Response serializer for DELETE /simulate/run-tests/{run_test_id}/eval-configs/{id}/  (HTTP 200)"""

    message = serializers.CharField()


class EvalTemplateSummarySerializer(serializers.Serializer):
    """Single evaluation template summary entry within EvalSummaryResponseSerializer."""

    name = serializers.CharField()
    average_score = serializers.FloatField(allow_null=True)
    total_runs = serializers.IntegerField()
    passed = serializers.IntegerField()
    failed = serializers.IntegerField()


class EvalSummaryResponseSerializer(serializers.Serializer):
    """Response serializer for GET /simulate/run-tests/{run_test_id}/eval-summary/  (HTTP 200)

    Returns an array of per-eval-config summary objects.
    """

    evaluations = EvalTemplateSummarySerializer(many=True)


class EvalSummaryComparisonResponseSerializer(serializers.Serializer):
    """Response serializer for GET /simulate/run-tests/{run_test_id}/eval-summary-comparison/  (HTTP 200)

    Returns a dict keyed by execution UUID, each value being an array of eval summary objects.
    The actual response is a plain dict — this serializer documents the shape.
    """

    pass  # Dynamic dict — documented in the MDX response section


class RunNewEvalsResponseSerializer(serializers.Serializer):
    """Response serializer for POST /simulate/run-tests/{run_test_id}/run-new-evals/  (HTTP 200)"""

    message = serializers.CharField()
    run_test_id = serializers.UUIDField()
    call_execution_count = serializers.IntegerField()


class EvalErrorResponseSerializer(serializers.Serializer):
    """Shared error response shape for all eval API endpoints."""

    error = serializers.CharField()
    details = serializers.DictField(required=False)
