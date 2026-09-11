from rest_framework import serializers

from simulate.serializers.run_test import SimulateEvalConfigSimpleSerializer
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer


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
    id = serializers.CharField()
    total_cells = serializers.IntegerField()
    output = serializers.JSONField()


class EvalSummaryResponseSerializer(serializers.Serializer):
    """Response serializer for GET /simulate/run-tests/{run_test_id}/eval-summary/  (HTTP 200)

    Returns an array of per-eval-config summary objects.
    """

    status = serializers.BooleanField(default=True)
    result = EvalTemplateSummarySerializer(many=True)


class EvalSummaryComparisonResponseSerializer(serializers.Serializer):
    """Response serializer for GET /simulate/run-tests/{run_test_id}/eval-summary-comparison/  (HTTP 200)

    Returns the GeneralMethods envelope. ``result`` is keyed by execution UUID,
    with each value being an array of eval summary objects.
    """

    status = serializers.BooleanField(default=True)
    result = serializers.DictField(
        child=serializers.ListField(child=EvalTemplateSummarySerializer())
    )


class EvalConfigStructureSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    template_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    reason_column = serializers.BooleanField(read_only=True)
    eval_tags = serializers.JSONField(read_only=True, allow_null=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    required_keys = serializers.ListField(child=serializers.CharField())
    optional_keys = serializers.ListField(child=serializers.CharField())
    variable_keys = serializers.ListField(child=serializers.CharField())
    run_prompt_column = serializers.BooleanField(read_only=True)
    template_name = serializers.CharField(read_only=True)
    mapping = serializers.DictField(read_only=True)
    config = serializers.DictField(read_only=True)
    params = serializers.JSONField(read_only=True, allow_null=True)
    function_params_schema = serializers.JSONField(read_only=True, allow_null=True)
    models = serializers.JSONField(read_only=True, allow_null=True)
    selected_model = serializers.CharField(read_only=True, allow_null=True)
    error_localizer = serializers.BooleanField(read_only=True)
    kb_id = serializers.UUIDField(read_only=True, allow_null=True)
    output = serializers.JSONField(read_only=True, allow_null=True)
    config_params_desc = serializers.DictField(read_only=True)
    config_params_option = serializers.DictField(read_only=True)
    api_key_available = serializers.BooleanField(read_only=True)


class EvalConfigStructureResultSerializer(serializers.Serializer):
    eval = EvalConfigStructureSerializer()


class EvalConfigStructureResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalConfigStructureResultSerializer()


class RunNewEvalsResponseSerializer(serializers.Serializer):
    """Response serializer for POST /simulate/run-tests/{run_test_id}/run-new-evals/  (HTTP 200)"""

    message = serializers.CharField()
    run_test_id = serializers.UUIDField()
    call_execution_count = serializers.IntegerField()


class EvalErrorResponseSerializer(ApiTextErrorResponseSerializer):
    """Shared error response shape for all eval API endpoints."""
