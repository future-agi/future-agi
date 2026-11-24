from rest_framework import serializers

from simulate.models import CallExecution, TestExecution

# ── Input Serializers ──


class _EvalNameMixin:
    """Shared eval_name parsing logic."""

    def validate_eval_name(self, value):
        if value:
            return [name.strip() for name in value.split(",") if name.strip()]
        return None


class SimulationQuerySerializer(serializers.Serializer):
    """Base query param serializer for all simulation endpoints."""

    run_test_name = serializers.CharField(required=False, allow_blank=False)
    execution_id = serializers.UUIDField(required=False)
    call_execution_id = serializers.UUIDField(required=False)

    def validate(self, data):
        if not any(
            data.get(k) for k in ("run_test_name", "execution_id", "call_execution_id")
        ):
            raise serializers.ValidationError(
                "At least one of 'run_test_name', 'execution_id', or 'call_execution_id' is required."
            )
        return data


class SimulationRunsQuerySerializer(_EvalNameMixin, SimulationQuerySerializer):
    """Query params for /simulation/runs/."""

    eval_name = serializers.CharField(required=False, allow_blank=False)
    summary = serializers.BooleanField(required=False, default=False)


class SimulationAnalyticsQuerySerializer(_EvalNameMixin, serializers.Serializer):
    """Query params for /simulation/analytics/."""

    run_test_name = serializers.CharField(required=False, allow_blank=False)
    execution_id = serializers.UUIDField(required=False)
    eval_name = serializers.CharField(required=False, allow_blank=False)
    summary = serializers.BooleanField(required=False, default=True)

    def validate(self, data):
        if not data.get("run_test_name") and not data.get("execution_id"):
            raise serializers.ValidationError(
                "At least one of 'run_test_name' or 'execution_id' is required."
            )
        return data


# ── Output Serializers: /simulation/metrics/ ──


_LATENCY_FIELDS = (
    "avg_agent_latency_ms",
    "response_time_ms",
)

_COST_FIELDS = (
    ("total_cost_cents", "cost_cents"),
    ("stt_cost_cents", "stt_cost_cents"),
    ("llm_cost_cents", "llm_cost_cents"),
    ("tts_cost_cents", "tts_cost_cents"),
)

_CONVERSATION_FIELDS = (
    "user_wpm",
    "bot_wpm",
    "talk_ratio",
    "user_interruption_count",
    "user_interruption_rate",
    "ai_interruption_count",
    "ai_interruption_rate",
    "avg_stop_time_after_interruption_ms",
)


class CallMetricsSerializer(serializers.ModelSerializer):
    """Per-call raw metrics."""

    call_execution_id = serializers.UUIDField(source="id")
    execution_id = serializers.UUIDField(source="test_execution_id")
    latency = serializers.SerializerMethodField()
    cost = serializers.SerializerMethodField()
    conversation = serializers.SerializerMethodField()
    chat_metrics = serializers.SerializerMethodField()

    class Meta:
        model = CallExecution
        fields = [
            "call_execution_id",
            "execution_id",
            "status",
            "duration_seconds",
            "latency",
            "cost",
            "conversation",
            "chat_metrics",
        ]
        read_only_fields = fields

    def get_latency(self, obj):
        result = {f: getattr(obj, f) for f in _LATENCY_FIELDS}
        # Extract system metrics from customer_latency_metrics if available
        metrics = obj.customer_latency_metrics
        if isinstance(metrics, dict):
            system_metrics = metrics.get("systemMetrics")
            if isinstance(system_metrics, dict):
                result["system_metrics"] = system_metrics
        return result

    def get_cost(self, obj):
        result = {
            output_name: getattr(obj, model_field)
            for output_name, model_field in _COST_FIELDS
        }
        breakdown = obj.customer_cost_breakdown
        if isinstance(breakdown, dict):
            result["breakdown"] = breakdown
        return result

    def get_conversation(self, obj):
        return {f: getattr(obj, f) for f in _CONVERSATION_FIELDS}

    def get_chat_metrics(self, obj):
        data = obj.conversation_metrics_data
        if not isinstance(data, dict):
            return None
        # Return only known safe fields
        safe_keys = (
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "avg_latency_ms",
            "turn_count",
            "csat_score",
            "message_count",
        )
        return {k: data[k] for k in safe_keys if k in data}


class ExecutionMetricsSerializer(serializers.ModelSerializer):
    """Per-execution aggregated metrics."""

    execution_id = serializers.UUIDField(source="id")
    metrics = serializers.SerializerMethodField()

    class Meta:
        model = TestExecution
        fields = [
            "execution_id",
            "status",
            "started_at",
            "completed_at",
            "total_calls",
            "completed_calls",
            "failed_calls",
            "metrics",
        ]
        read_only_fields = fields

    def get_metrics(self, obj):
        return self.context.get("metrics_map", {}).get(str(obj.id), {})


# ── Output Serializers: /simulation/runs/ ──


class CallRunDetailSerializer(serializers.ModelSerializer):
    """Full detail for a single call execution in /simulation/runs/."""

    call_execution_id = serializers.UUIDField(source="id")
    execution_id = serializers.UUIDField(source="test_execution_id")
    scenario_id = serializers.UUIDField()
    scenario_name = serializers.CharField(source="scenario.name", read_only=True)
    eval_outputs = serializers.SerializerMethodField()
    latency = serializers.SerializerMethodField()
    cost = serializers.SerializerMethodField()

    class Meta:
        model = CallExecution
        fields = [
            "call_execution_id",
            "execution_id",
            "scenario_id",
            "scenario_name",
            "status",
            "started_at",
            "completed_at",
            "duration_seconds",
            "ended_reason",
            "call_summary",
            "eval_outputs",
            "latency",
            "cost",
        ]
        read_only_fields = fields

    def get_eval_outputs(self, obj):
        eval_outputs = obj.eval_outputs or {}
        eval_names = self.context.get("eval_names")
        if eval_names:
            return _filter_eval_outputs_by_name(eval_outputs, eval_names)
        return eval_outputs

    def get_latency(self, obj):
        return {f: getattr(obj, f) for f in _LATENCY_FIELDS}

    def get_cost(self, obj):
        return {
            output_name: getattr(obj, model_field)
            for output_name, model_field in _COST_FIELDS
        }


class CallRunSummarySerializer(serializers.ModelSerializer):
    """Summary for a call execution in paginated call results."""

    call_execution_id = serializers.UUIDField(source="id")
    scenario_id = serializers.UUIDField()
    scenario_name = serializers.CharField(source="scenario.name", read_only=True)
    eval_outputs = serializers.SerializerMethodField()

    class Meta:
        model = CallExecution
        fields = [
            "call_execution_id",
            "scenario_id",
            "scenario_name",
            "status",
            "duration_seconds",
            "eval_outputs",
        ]
        read_only_fields = fields

    def get_eval_outputs(self, obj):
        eval_outputs = obj.eval_outputs or {}
        eval_names = self.context.get("eval_names")
        if eval_names:
            return _filter_eval_outputs_by_name(eval_outputs, eval_names)
        return eval_outputs


class ExecutionRunsSerializer(serializers.ModelSerializer):
    """Per-execution summary for /simulation/runs/ list view."""

    execution_id = serializers.UUIDField(source="id")
    eval_results = serializers.SerializerMethodField()

    class Meta:
        model = TestExecution
        fields = [
            "execution_id",
            "status",
            "started_at",
            "completed_at",
            "total_calls",
            "completed_calls",
            "failed_calls",
            "eval_results",
        ]
        read_only_fields = fields

    def get_eval_results(self, obj):
        return self.context.get("eval_results_map", {}).get(str(obj.id), [])


# ── Output Serializers: /simulation/analytics/ ──


class AnalyticsResponseSerializer(serializers.Serializer):
    """Response for /simulation/analytics/ endpoint.

    Note: eval_explanation_summary, eval_explanation_summary_status and
    eval_explanation_summary_requested are conditionally added by the view when summary=true, so they are not
    declared as fields here. The serializer passes them through via
    to_representation.
    """

    execution_id = serializers.UUIDField()
    run_test_name = serializers.CharField()
    status = serializers.CharField()
    eval_results = serializers.ListField()
    eval_averages = serializers.DictField()
    system_summary = serializers.DictField()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if "eval_explanation_summary" in instance:
            data["eval_explanation_summary"] = instance["eval_explanation_summary"]
            data["eval_explanation_summary_status"] = instance.get(
                "eval_explanation_summary_status"
            )
            data["eval_explanation_summary_requested"] = instance.get(
                "eval_explanation_summary_requested"
            )
        return data


# ── Helpers ──


def _filter_eval_outputs_by_name(eval_outputs, eval_names):
    if not eval_outputs or not eval_names:
        return eval_outputs
    eval_names_lower = {name.lower() for name in eval_names}
    return {
        config_id: data
        for config_id, data in eval_outputs.items()
        if isinstance(data, dict) and data.get("name", "").lower() in eval_names_lower
    }
