"""Isolated v3 APIs for simulation run results."""

from __future__ import annotations

import csv
import json
import uuid
from collections.abc import Iterator
from itertools import islice
from typing import Any

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import CallExecution, TestExecution
from simulate.serializers.run_dashboard_v3 import RunDashboardV3Serializer
from simulate.serializers.test_execution import CallExecutionDetailSerializer
from simulate.services.run_results_v3 import (
    build_call_rows,
    build_evaluation_catalog,
    function_calls,
)
from simulate.services.run_results_v3_queries import (
    GROUP_FIELDS,
    apply_run_call_query,
    build_run_analytics,
    group_run_calls,
    run_call_facets,
    run_calls_queryset,
    summarize_run_calls,
)
from simulate.services.test_executor import build_eval_configs_map
from simulate.views.scoping import run_test_workspace_filter
from tfc.utils.api_contracts import validated_request


class JsonObjectField(serializers.JSONField):
    """Accept a JSON object both in query strings and request bodies."""

    def to_internal_value(self, data):
        if data in (None, ""):
            return {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Value must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise serializers.ValidationError("Value must be an object.")
        return data


class RunCallFiltersSerializer(serializers.Serializer):
    search = serializers.CharField(default="", allow_blank=True, required=False)
    filters = JsonObjectField(default=dict, required=False)
    ordering = serializers.ChoiceField(
        choices=[
            "started_at",
            "-started_at",
            "duration_seconds",
            "-duration_seconds",
            "latency_ms",
            "-latency_ms",
            "turn_count",
            "-turn_count",
            "tokens",
            "-tokens",
            "cost_cents",
            "-cost_cents",
            "scenario",
            "-scenario",
            "goal",
            "-goal",
            "outcome",
            "-outcome",
        ],
        default="-started_at",
        required=False,
    )

    def validate_filters(self, value):
        allowed = {"goal", "sub_goal", "status", "call_execution_id"}
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported filters: {', '.join(sorted(unknown))}."
            )
        for field, selected in value.items():
            if not isinstance(selected, list) or not all(
                isinstance(item, str) for item in selected
            ):
                raise serializers.ValidationError(
                    f"Filter '{field}' must be a list of strings."
                )
        invalid_statuses = set(value.get("status", [])) - {
            "passed",
            "failed",
            "error",
            "inconclusive",
        }
        if invalid_statuses:
            raise serializers.ValidationError(
                f"Unsupported statuses: {', '.join(sorted(invalid_statuses))}."
            )
        for call_id in value.get("call_execution_id", []):
            try:
                uuid.UUID(call_id)
            except ValueError as exc:
                raise serializers.ValidationError(
                    f"Invalid call_execution_id: {call_id}."
                ) from exc
        return value


class RunCallsV3QuerySerializer(RunCallFiltersSerializer):
    page = serializers.IntegerField(default=1, min_value=1, required=False)
    page_size = serializers.IntegerField(
        default=50, min_value=1, max_value=500, required=False
    )
    group_by = serializers.ChoiceField(
        choices=list(GROUP_FIELDS), allow_blank=True, required=False, default=""
    )
    group_key = serializers.CharField(allow_blank=True, required=False, allow_null=True)


class RunExportV3RequestSerializer(RunCallFiltersSerializer):
    pass


class MetricStatsSerializer(serializers.Serializer):
    average = serializers.FloatField(allow_null=True)
    p50 = serializers.FloatField(allow_null=True)
    p75 = serializers.FloatField(allow_null=True)
    p90 = serializers.FloatField(allow_null=True)
    p95 = serializers.FloatField(allow_null=True)
    p99 = serializers.FloatField(allow_null=True)
    measured = serializers.IntegerField()
    total = serializers.IntegerField()


class TotalMetricStatsSerializer(MetricStatsSerializer):
    total_value = serializers.FloatField(allow_null=True)


class OutcomeCountsSerializer(serializers.Serializer):
    passed = serializers.IntegerField()
    failed = serializers.IntegerField()
    error = serializers.IntegerField()
    inconclusive = serializers.IntegerField()


class RunSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    outcomes = OutcomeCountsSerializer()
    measured = serializers.IntegerField()
    pass_rate = serializers.FloatField(allow_null=True)
    duration = MetricStatsSerializer()
    latency = MetricStatsSerializer()
    tokens = TotalMetricStatsSerializer()
    cost_cents = TotalMetricStatsSerializer()


class RunExecutionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    run_test_id = serializers.UUIDField()
    name = serializers.CharField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    ordinal = serializers.IntegerField()
    agent_version = serializers.CharField(allow_null=True)
    selected_scenario_keys = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    trials = serializers.IntegerField(required=False, min_value=1, default=1)
    agent_type = serializers.CharField(allow_null=True)
    summary = RunSummarySerializer()


class PersonaDetailsSerializer(serializers.Serializer):
    name = serializers.CharField(allow_null=True)
    voice = serializers.CharField(allow_null=True)
    age = serializers.CharField(allow_null=True)
    traits = serializers.ListField(child=serializers.CharField())


class EvaluationResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    value = serializers.JSONField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)
    reason = serializers.CharField(allow_blank=True)
    status = serializers.CharField()

    class Meta:
        ref_name = "SimulateRunV3EvaluationResult"


class CostBreakdownSerializer(serializers.Serializer):
    stt = serializers.FloatField(allow_null=True)
    llm = serializers.FloatField(allow_null=True)
    tts = serializers.FloatField(allow_null=True)
    storage = serializers.FloatField(allow_null=True)
    customer = serializers.FloatField(allow_null=True)


class RunCallSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    scenario = serializers.CharField()
    scenario_details = serializers.CharField(allow_null=True)
    goal = serializers.CharField()
    ideal_outcome = serializers.CharField(allow_null=True)
    conversation_branch = serializers.CharField(allow_null=True)
    persona = serializers.CharField(allow_null=True)
    persona_details = PersonaDetailsSerializer(allow_null=True)
    sub_goals = serializers.ListField(child=serializers.CharField())
    harness_outcome_status = serializers.CharField(allow_null=True)
    source_scenario_key = serializers.CharField(allow_null=True)
    trial_index = serializers.IntegerField(allow_null=True)
    outcome = serializers.ChoiceField(
        choices=["passed", "failed", "error", "inconclusive"]
    )
    execution_status = serializers.CharField()
    modality = serializers.CharField()
    provider = serializers.CharField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.FloatField(allow_null=True)
    latency_ms = serializers.FloatField(allow_null=True)
    turn_count = serializers.IntegerField(allow_null=True)
    tokens = serializers.IntegerField(allow_null=True)
    cost_cents = serializers.FloatField(allow_null=True)
    cost_breakdown_cents = CostBreakdownSerializer()
    csat = serializers.FloatField(allow_null=True)
    ended_reason = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(allow_null=True)
    evaluations = EvaluationResultSerializer(many=True)


class FacetValueSerializer(serializers.Serializer):
    value = serializers.CharField()
    count = serializers.IntegerField()


class RunFacetsSerializer(serializers.Serializer):
    goal = FacetValueSerializer(many=True)
    sub_goal = FacetValueSerializer(many=True)
    status = FacetValueSerializer(many=True)


class EvaluationColumnSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class GroupAggregatesSerializer(serializers.Serializer):
    csat = serializers.FloatField(allow_null=True)
    turns = serializers.FloatField(allow_null=True)
    latency_ms = serializers.FloatField(allow_null=True)
    tokens = serializers.FloatField(allow_null=True)
    evaluations = serializers.JSONField()


class RunGroupSerializer(RunSummarySerializer):
    key = serializers.CharField()
    label = serializers.CharField()
    result_ids = serializers.ListField(child=serializers.UUIDField())
    aggregates = GroupAggregatesSerializer()


class RunCallsV3ResponseSerializer(serializers.Serializer):
    execution = RunExecutionSerializer()
    summary = RunSummarySerializer()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    results = RunCallSerializer(many=True)
    groups = RunGroupSerializer(many=True)
    facets = RunFacetsSerializer()
    evaluation_columns = EvaluationColumnSerializer(many=True)


class AnalyticsExecutionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class AnalyticsSummarySerializer(RunSummarySerializer):
    evaluators = serializers.IntegerField()


class RiskSerializer(RunSummarySerializer):
    goal = serializers.CharField()


class TurnDistributionSerializer(OutcomeCountsSerializer):
    turn_count = serializers.IntegerField()


class EvaluationSummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    passed = serializers.IntegerField()
    failed = serializers.IntegerField()
    measured = serializers.IntegerField()
    missing = serializers.IntegerField()
    pass_rate = serializers.FloatField(allow_null=True)
    average_score = serializers.FloatField(allow_null=True)


class FailureBreakdownSerializer(serializers.Serializer):
    reason = serializers.CharField()
    failures = serializers.IntegerField()
    share = serializers.FloatField()


class CostComponentSerializer(serializers.Serializer):
    total = serializers.FloatField(allow_null=True)
    measured = serializers.IntegerField()
    calls = serializers.IntegerField()


class CostComponentsSerializer(serializers.Serializer):
    stt = CostComponentSerializer()
    llm = CostComponentSerializer()
    tts = CostComponentSerializer()
    storage = CostComponentSerializer()
    customer = CostComponentSerializer()


class DistributionsSerializer(serializers.Serializer):
    duration_seconds = MetricStatsSerializer()
    latency_ms = MetricStatsSerializer()
    tokens = TotalMetricStatsSerializer()
    cost_cents = TotalMetricStatsSerializer()


class ProviderBreakdownSerializer(RunSummarySerializer):
    provider = serializers.CharField()


class ModalityBreakdownSerializer(RunSummarySerializer):
    modality = serializers.CharField()


class TrendSerializer(RunSummarySerializer):
    execution_id = serializers.UUIDField()
    started_at = serializers.DateTimeField(allow_null=True)


class RunAnalyticsV3ResponseSerializer(serializers.Serializer):
    dashboard = RunDashboardV3Serializer()
    execution = AnalyticsExecutionSerializer()
    summary = AnalyticsSummarySerializer()
    scenario_risk = RiskSerializer(many=True)
    turn_distribution = TurnDistributionSerializer(many=True)
    evaluations = EvaluationSummarySerializer(many=True)
    failure_breakdown = FailureBreakdownSerializer(many=True)
    distributions = DistributionsSerializer()
    cost_breakdown_cents = CostComponentsSerializer()
    provider_breakdown = ProviderBreakdownSerializer(many=True)
    modality_breakdown = ModalityBreakdownSerializer(many=True)
    trends = TrendSerializer(many=True)


class FunctionCallSerializer(serializers.Serializer):
    id = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    arguments = serializers.JSONField(required=False)
    result = serializers.JSONField(required=False)
    output = serializers.JSONField(required=False)
    duration_ms = serializers.FloatField(required=False)

    class Meta:
        ref_name = "SimulateRunV3FunctionCall"


class CallExecutionV3DetailResponseSerializer(CallExecutionDetailSerializer):
    goal = serializers.CharField()
    scenario_details = serializers.CharField(allow_null=True)
    ideal_outcome = serializers.CharField(allow_null=True)
    conversation_branch = serializers.CharField(allow_null=True)
    persona = serializers.CharField(allow_null=True)
    persona_details = PersonaDetailsSerializer(allow_null=True)
    sub_goals = serializers.ListField(child=serializers.CharField())
    outcome = serializers.ChoiceField(
        choices=["passed", "failed", "error", "inconclusive"]
    )
    cost_breakdown_cents = CostBreakdownSerializer()
    evaluations = EvaluationResultSerializer(many=True)
    function_calls = FunctionCallSerializer(many=True)

    class Meta(CallExecutionDetailSerializer.Meta):
        fields = [
            *CallExecutionDetailSerializer.Meta.fields,
            "goal",
            "scenario_details",
            "ideal_outcome",
            "conversation_branch",
            "persona",
            "persona_details",
            "sub_goals",
            "outcome",
            "cost_breakdown_cents",
            "evaluations",
            "function_calls",
        ]


def _execution_for_request(request, execution_id) -> TestExecution:
    organization = getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )
    return get_object_or_404(
        TestExecution.objects.select_related(
            "run_test", "agent_definition", "agent_version", "run_test__agent_version"
        ),
        run_test_workspace_filter(request, "run_test"),
        id=execution_id,
        run_test__organization=organization,
        run_test__deleted=False,
    )


def _execution_payload(
    execution: TestExecution, summary: dict[str, Any]
) -> dict[str, Any]:
    version = execution.agent_version or execution.run_test.agent_version
    ordinal = TestExecution.objects.filter(
        run_test=execution.run_test, created_at__lte=execution.created_at
    ).count()
    return {
        "id": str(execution.id),
        "run_test_id": str(execution.run_test_id),
        "name": execution.run_test.name,
        "status": execution.status,
        "started_at": execution.started_at,
        "completed_at": execution.completed_at,
        "ordinal": ordinal,
        "agent_version": getattr(version, "version_name", None),
        "agent_type": (
            execution.agent_definition.agent_type
            if execution.agent_definition
            else None
        ),
        "selected_scenario_keys": list(
            (execution.execution_metadata or {}).get("selected_scenario_keys") or []
        ),
        "trials": execution.trials or 1,
        "summary": summary,
    }


class RunCallsV3View(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=RunCallsV3QuerySerializer,
        responses={200: RunCallsV3ResponseSerializer},
        operation_id="simulate_v3_test_execution_calls",
    )
    def get(self, request, test_execution_id, *args, **kwargs):
        query = request.validated_query_data
        execution = _execution_for_request(request, test_execution_id)
        base_queryset = run_calls_queryset(execution)
        filtered_queryset = apply_run_call_query(base_queryset, query)
        page = query["page"]
        page_size = query["page_size"]
        start = (page - 1) * page_size
        filtered_summary = summarize_run_calls(
            filtered_queryset, include_percentiles=False
        )
        count = filtered_summary["total"]
        columns, live_eval_ids = build_evaluation_catalog(execution)
        page_calls = list(filtered_queryset[start : start + page_size])
        page_rows, columns = build_call_rows(
            execution, page_calls, columns, live_eval_ids
        )
        has_subset = bool(
            query.get("search")
            or query.get("filters")
            or query.get("group_key") is not None
        )
        execution_summary = (
            summarize_run_calls(base_queryset, include_percentiles=False)
            if has_subset
            else filtered_summary
        )
        facets_cache_key = None
        # Facets span the whole run so filter options never vanish, except under
        # a hand-off of specific calls, which scopes the counts too.
        facet_queryset = base_queryset
        if call_ids := (query.get("filters") or {}).get("call_execution_id"):
            facet_queryset = apply_run_call_query(
                base_queryset, {"filters": {"call_execution_id": call_ids}}
            )
        elif execution.status == TestExecution.ExecutionStatus.COMPLETED:
            version = execution.completed_at or execution.updated_at
            facets_cache_key = (
                f"simulate:v3:facets:{execution.id}:{version.timestamp()}"
            )
        response = {
            "execution": _execution_payload(execution, execution_summary),
            "summary": filtered_summary,
            "count": count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (count + page_size - 1) // page_size),
            "results": page_rows,
            "groups": group_run_calls(
                filtered_queryset, query.get("group_by"), page_rows, columns
            ),
            "facets": run_call_facets(facet_queryset, facets_cache_key),
            "evaluation_columns": columns,
        }
        return Response(response)


class CallExecutionV3DetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: CallExecutionV3DetailResponseSerializer},
        operation_id="simulate_v3_call_execution_detail",
    )
    def get(self, request, call_execution_id, *args, **kwargs):
        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        call = get_object_or_404(
            CallExecution.objects.select_related(
                "scenario",
                "test_execution__run_test",
                "test_execution__agent_definition",
                "test_execution__simulator_agent",
            ).prefetch_related("transcripts", "chat_messages", "snapshots"),
            run_test_workspace_filter(request, "test_execution__run_test"),
            id=call_execution_id,
            test_execution__run_test__organization=organization,
            test_execution__run_test__deleted=False,
        )
        row, _ = build_call_rows(call.test_execution, [call])
        data = dict(
            CallExecutionDetailSerializer(
                call,
                context={
                    "request": request,
                    "eval_configs": build_eval_configs_map(call),
                    "detail_mode": True,
                },
            ).data
        )
        normalized = row[0]
        data.update(
            {
                "goal": normalized["goal"],
                "scenario_details": normalized["scenario_details"],
                "ideal_outcome": normalized["ideal_outcome"],
                "conversation_branch": normalized["conversation_branch"],
                "persona": normalized["persona"],
                "persona_details": normalized["persona_details"],
                "sub_goals": normalized["sub_goals"],
                "outcome": normalized["outcome"],
                "cost_breakdown_cents": normalized["cost_breakdown_cents"],
                "evaluations": normalized["evaluations"],
                "function_calls": function_calls(call),
            }
        )
        return Response(data)


class RunAnalyticsV3View(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: RunAnalyticsV3ResponseSerializer},
        operation_id="simulate_v3_test_execution_analytics",
    )
    def get(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        return Response(build_run_analytics(execution))


class _CsvEcho:
    def write(self, value: str) -> str:
        return value


def _escape_csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _csv_rows(
    rows: list[dict[str, Any]], columns: list[dict[str, str]]
) -> Iterator[str]:
    eval_headers = [f"evaluation:{column['name']}" for column in columns]
    headers = [
        "call_id",
        "scenario",
        "scenario_details",
        "goal",
        "ideal_outcome",
        "conversation_branch",
        "sub_goals",
        "persona",
        "outcome",
        "execution_status",
        "provider",
        "modality",
        "duration_seconds",
        "latency_ms",
        "turn_count",
        "tokens",
        "cost_cents",
        "csat",
        "ended_reason",
        "error_message",
        *eval_headers,
    ]
    writer = csv.writer(_CsvEcho())
    yield writer.writerow([_escape_csv_cell(value) for value in headers])
    for row in rows:
        evaluations = {item["id"]: item.get("value") for item in row["evaluations"]}
        yield writer.writerow(
            [
                _escape_csv_cell(value)
                for value in [
                    row["id"],
                    row["scenario"],
                    row["scenario_details"],
                    row["goal"],
                    row["ideal_outcome"],
                    row["conversation_branch"],
                    ", ".join(row["sub_goals"]),
                    row["persona"],
                    row["outcome"],
                    row["execution_status"],
                    row["provider"],
                    row["modality"],
                    row["duration_seconds"],
                    row["latency_ms"],
                    row["turn_count"],
                    row["tokens"],
                    row["cost_cents"],
                    row["csat"],
                    row["ended_reason"],
                    row["error_message"],
                    *[evaluations.get(column["id"]) for column in columns],
                ]
            ]
        )


def _csv_rows_from_queryset(execution: TestExecution, queryset) -> Iterator[str]:
    columns, live_eval_ids = build_evaluation_catalog(execution)
    yield from _csv_rows([], columns)
    iterator = queryset.iterator(chunk_size=500)
    while chunk := list(islice(iterator, 500)):
        rows, _ = build_call_rows(execution, chunk, columns, live_eval_ids)
        chunk_rows = _csv_rows(rows, columns)
        next(chunk_rows)
        yield from chunk_rows


class RunExportV3View(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=RunExportV3RequestSerializer,
        responses={200: openapi.Response("Filtered simulation calls CSV")},
        operation_id="simulate_v3_test_execution_export",
    )
    def post(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        queryset = apply_run_call_query(
            run_calls_queryset(execution), request.validated_data
        )
        response = StreamingHttpResponse(
            _csv_rows_from_queryset(execution, queryset),
            content_type="text/csv; charset=utf-8",
            status=status.HTTP_200_OK,
        )
        response["Content-Disposition"] = (
            f'attachment; filename="simulation-run-{execution.id}.csv"'
        )
        return response
