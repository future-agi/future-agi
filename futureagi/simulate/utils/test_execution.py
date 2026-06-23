import traceback

import structlog

logger = structlog.get_logger(__name__)
from simulate.models import (
    CallExecution,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.serializers.test_execution import CallExecutionDetailSerializer


def calculate_aggregate_metrics(call_executions):
    """
    Calculates aggregate metrics from a queryset of call executions.
    """
    try:
        total_calls = call_executions.count()
        if total_calls == 0:
            return {}

        completed_calls = call_executions.filter(
            status=CallExecution.CallStatus.COMPLETED
        ).count()
        success_rate = round((completed_calls / total_calls * 100), 1)

        pending_calls = call_executions.filter(
            status=CallExecution.CallStatus.PENDING
        ).count()
        queued_calls = call_executions.filter(
            status=CallExecution.CallStatus.REGISTERED
        ).count()
        failed_calls = call_executions.filter(
            status=CallExecution.CallStatus.FAILED
        ).count()
        call_executions.filter(
            status=CallExecution.CallStatus.CANCELLED
        ).count()
        calls_attempted = total_calls - pending_calls - queued_calls
        connected_calls = call_executions.filter(duration_seconds__gt=0).count()
        calls_connected_percentage = (
            round((connected_calls / calls_attempted * 100), 2)
            if calls_attempted > 0
            else 0.0
        )

        valid_scores, valid_response_times = [], []
        valid_agent_latencies, valid_user_interruption_counts = [], []
        valid_talk_ratios = []
        eval_scores = {}

        for call_execution in call_executions:
            if call_execution.overall_score is not None:
                valid_scores.append(call_execution.overall_score)
            if call_execution.response_time_ms is not None:
                valid_response_times.append(call_execution.response_time_ms)

            if call_execution.avg_agent_latency_ms is not None:
                valid_agent_latencies.append(call_execution.avg_agent_latency_ms)
            if call_execution.user_interruption_count is not None:
                valid_user_interruption_counts.append(
                    call_execution.user_interruption_count
                )
            if call_execution.talk_ratio is not None:
                valid_talk_ratios.append(call_execution.talk_ratio)

            if call_execution.eval_outputs and isinstance(
                call_execution.eval_outputs, dict
            ):
                for metric_id, metric_data in call_execution.eval_outputs.items():
                    if isinstance(metric_data, dict) and "output" in metric_data:
                        metric_name = metric_data.get("name", f"metric_{metric_id}")
                        if metric_name not in eval_scores:
                            eval_scores[metric_name] = []
                        output_type = metric_data.get("output_type")
                        output_value = metric_data.get("output")
                        if output_type == "Pass/Fail":
                            eval_scores[metric_name].append(
                                100.0 if output_value == "Passed" else 0.0
                            )
                        elif output_type == "score" and isinstance(
                            output_value, (int, float)
                        ):
                            eval_scores[metric_name].append(float(output_value) * 100)

        avg_score = (
            round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 0
        )
        avg_response = (
            round(sum(valid_response_times) / len(valid_response_times))
            if valid_response_times
            else 0
        )
        avg_agent_latency = (
            round(sum(valid_agent_latencies) / len(valid_agent_latencies))
            if valid_agent_latencies
            else 0
        )
        avg_user_interruption_count = (
            round(
                sum(valid_user_interruption_counts)
                / len(valid_user_interruption_counts),
                1,
            )
            if valid_user_interruption_counts
            else 0
        )
        avg_talk_ratio = (
            round(sum(valid_talk_ratios) / len(valid_talk_ratios), 2)
            if valid_talk_ratios
            else 0
        )
        agent_talk_percentage = (
            round((avg_talk_ratio / (avg_talk_ratio + 1)) * 100, 1)
            if avg_talk_ratio > 0
            else 0
        )
        customer_talk_percentage = (
            round((1 / (avg_talk_ratio + 1)) * 100, 1) if avg_talk_ratio > 0 else 0
        )

        eval_averages = {}
        for metric_name, scores in eval_scores.items():
            if scores:
                field_name = f"avg_{metric_name.lower().replace(' ', '_')}"
                eval_averages[field_name] = round(sum(scores) / len(scores), 1)

        aggregate_metrics_data = {
            "total_calls": total_calls,
            "success_rate": success_rate,
            "avg_score": avg_score,
            "avg_response": avg_response,
            "calls_attempted": calls_attempted,
            "connected_calls": connected_calls,
            "calls_connected_percentage": calls_connected_percentage,
            "avg_agent_latency": avg_agent_latency,
            "avg_user_interruption_count": avg_user_interruption_count,
            "agent_talk_percentage": agent_talk_percentage,
            "customer_talk_percentage": customer_talk_percentage,
            "failed_calls": failed_calls,
            **eval_averages,
        }
        return aggregate_metrics_data
    except Exception as e:
        logger.exception(f"Failed to calculate aggregate metrics: {str(e)}")
        return {}


def get_test_execution_results(test_execution_id):
    """
    Get a specific test execution with all its details and call executions.
    This function returns all results without pagination, filtering, or sorting.
    """
    try:
        # Get the test execution
        test_execution = TestExecution.objects.get(
            id=test_execution_id,
            run_test__deleted=False,
        )

        # Get call executions for this test execution
        call_executions = (
            CallExecution.objects.filter(test_execution=test_execution)
            .select_related(
                "scenario",
                "test_execution",
                "test_execution__simulator_agent",
                "test_execution__agent_definition",
            )
            .prefetch_related("transcripts", "snapshots")
        )

        # Calculate aggregate metrics
        aggregate_metrics = calculate_aggregate_metrics(call_executions)

        # Get eval configs
        eval_configs = SimulateEvalConfig.objects.filter(
            run_test=test_execution.run_test, deleted=False
        )
        eval_configs_map = {str(config.id): config for config in eval_configs}

        # Get scenarios for dynamic columns
        scenarios = Scenarios.objects.filter(
            id__in=test_execution.scenario_ids, deleted=False
        )
        scenarios_map = {str(scenario.id): scenario for scenario in scenarios}

        # Serialize the call executions
        call_executions_serializer = CallExecutionDetailSerializer(
            call_executions,
            many=True,
            context={"eval_configs": eval_configs_map, "scenarios": scenarios_map},
        )

        # Construct response data
        response_data = {
            "results": call_executions_serializer.data,
            "aggregate_metrics": aggregate_metrics,
        }

        return response_data

    except Exception as e:
        traceback.print_exc()
        return {"error": f"Failed to retrieve test execution results: {str(e)}"}


DEFAULT_VOICE_SIM_COL = [
    {"column_name": "Timestamp", "id": "timestamp", "visible": True},
    {
        "column_name": "Call Details",
        "id": "call_details",
        "visible": True,
    },
    {
        "column_name": "Overall Score",
        "id": "overall_score",
        "visible": False,
    },
    {
        "column_name": "Agent interruption",
        "id": "ai_interruption_count",
        "visible": False,
    },
    {
        "column_name": "Simulator interruption",
        "id": "user_interruption_count",
        "visible": False,
    },
    {
        "column_name": "Turn Count",
        "id": "turn_count",
        "visible": True,
    },
    {
        "column_name": "Agent Talk (%)",
        "id": "agent_talk_percentage",
        "visible": True,
    },
    {"column_name": "Latency", "id": "latency", "visible": False},
    {
        "column_name": "Provider Call ID",
        "id": "service_provider_call_id",
        "visible": False,
    },
]


DEFAULT_CHAT_SIM_COL = [
    {
        "column_name": "Chat Details",
        "id": "call_details",
        "visible": True,
    },
    {
        "column_name": "Overall Score",
        "id": "overall_score",
        "visible": False,
    },
    {
        "column_name": "Total Tokens",
        "id": "total_tokens",  # This is the total tokens from the conversation
        "visible": False,
    },
    {
        # Note: input_tokens = simulator tokens (role="user" under our chat dashboard convention)
        "column_name": "Input Tokens",
        "id": "input_tokens",
        "visible": False,
    },
    {
        # Note: output_tokens = agent tokens (role="assistant" under our chat dashboard convention)
        "column_name": "Output Tokens",
        "id": "output_tokens",
        "visible": False,
    },
    {
        "column_name": "Average Latency (ms)",
        "id": "avg_latency_ms",
        "visible": False,
    },
    {
        "column_name": "Turn Count",
        "id": "turn_count",
        "visible": False,
    },
]


# Legacy camelCase → snake_case id map for column_order entries persisted before
# the DRF camel-case middleware was removed. The frontend grid matches cell
# renderers and value selectors by snake_case ids, so any stored execution
# metadata carrying old camelCase ids renders empty cells until normalized.
LEGACY_SIM_COLUMN_ID_MAP = {
    "callDetails": "call_details",
    "overallScore": "overall_score",
    "aiInterruptionCount": "ai_interruption_count",
    "userInterruptionCount": "user_interruption_count",
    "turnCount": "turn_count",
    "agentTalkPercentage": "agent_talk_percentage",
    "serviceProviderCallId": "service_provider_call_id",
    "totalTokens": "total_tokens",
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "avgLatencyMs": "avg_latency_ms",
}
