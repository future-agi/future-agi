import structlog
from django.db import connection
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from sdk.serializers.analytics import (
    AnalyticsResponseSerializer,
    CallMetricsSerializer,
    CallRunDetailSerializer,
    CallRunSummarySerializer,
    ExecutionMetricsSerializer,
    ExecutionRunsSerializer,
    SimulationAnalyticsQuerySerializer,
    SimulationQuerySerializer,
    SimulationRunsQuerySerializer,
)
from sdk.serializers.contracts import (
    SDKErrorResponseSerializer,
    SDKSimulationAnalyticsResponseSerializer,
    SDKSimulationMetricsResponseSerializer,
    SDKSimulationRunsResponseSerializer,
)
from sdk.utils.api_errors import sdk_validation_error_response
from simulate.models import CallExecution, TestExecution
from simulate.models.run_test import RunTest
from simulate.utils.eval_summary import (
    _build_template_statistics,
    _calculate_final_template_summaries,
    _get_completed_call_executions,
    _get_eval_configs_with_template,
)
from simulate.utils.sql_query import get_kpi_eval_metrics_query, get_kpi_metrics_query
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


def _compute_latency_percentiles_sql(execution_id):
    """Compute latency percentiles using SQL instead of loading all values into memory."""
    query = """
    SELECT
        ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY avg_agent_latency_ms)::numeric, 1) AS p50,
        ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY avg_agent_latency_ms)::numeric, 1) AS p95,
        ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY avg_agent_latency_ms)::numeric, 1) AS p99
    FROM simulate_call_execution
    WHERE test_execution_id = %s
      AND avg_agent_latency_ms IS NOT NULL
    """
    with connection.cursor() as cursor:
        cursor.execute(query, [str(execution_id)])
        row = cursor.fetchone()

    if not row or row[0] is None:
        return {}

    return {
        "p50": float(row[0]),
        "p95": float(row[1]),
        "p99": float(row[2]),
    }


class SimulationMetricsView(APIView):
    """
    GET /simulation/metrics/

    Aggregated system metrics: latency (by subsystem), cost, conversation metrics.

    Params:
    - run_test_name: paginated per-execution aggregated metrics
    - execution_id: aggregated metrics for one execution
    - call_execution_id: raw metrics for one call
    """

    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)

    @validated_request(
        query_serializer=SimulationQuerySerializer,
        responses={
            200: SDKSimulationMetricsResponseSerializer,
            400: SDKErrorResponseSerializer,
            404: SDKErrorResponseSerializer,
            500: SDKErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=sdk_validation_error_response,
        framework_query_params=("page", "limit"),
    )
    def get(self, request, *args, **kwargs):
        try:
            organization = getattr(request, "organization", None) or getattr(
                request.user, "organization", None
            )
            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            data = request.validated_query_data
            run_test_name = data.get("run_test_name")
            execution_id = data.get("execution_id")
            call_execution_id = data.get("call_execution_id")

            if call_execution_id:
                return self._handle_call_execution(call_execution_id, organization)

            if execution_id:
                return self._handle_execution(execution_id, organization)

            return self._handle_run_test(request, run_test_name, organization)

        except Exception:
            logger.exception("analytics_metrics_error")
            return self._gm.internal_server_error_response(
                get_error_message("ANALYTICS_METRICS_ERROR")
            )

    def _handle_call_execution(self, call_execution_id, organization):
        try:
            call = CallExecution.objects.select_related("test_execution__run_test").get(
                id=call_execution_id,
                test_execution__run_test__organization=organization,
            )
        except CallExecution.DoesNotExist:
            return self._gm.not_found(
                get_error_message("ANALYTICS_CALL_EXECUTION_NOT_FOUND")
            )

        output = CallMetricsSerializer(call).data
        return self._gm.success_response(output)

    def _handle_execution(self, execution_id, organization):
        try:
            execution = TestExecution.objects.select_related("run_test").get(
                id=execution_id, run_test__organization=organization
            )
        except TestExecution.DoesNotExist:
            return self._gm.not_found(
                get_error_message("ANALYTICS_EXECUTION_NOT_FOUND")
            )

        metrics = self._aggregate_execution_metrics(str(execution.id))
        output = ExecutionMetricsSerializer(
            execution, context={"metrics_map": {str(execution.id): metrics}}
        ).data
        return self._gm.success_response(output)

    def _handle_run_test(self, request, run_test_name, organization):
        run_tests = RunTest.objects.filter(
            name=run_test_name, organization=organization
        )
        if not run_tests.exists():
            return self._gm.not_found(get_error_message("ANALYTICS_RUN_TEST_NOT_FOUND"))

        executions = (
            TestExecution.objects.filter(run_test__in=run_tests)
            .select_related("run_test")
            .order_by("-created_at")
        )

        paginator = ExtendedPageNumberPagination()
        paginated_executions = paginator.paginate_queryset(executions, request)

        # Batch: collect execution IDs, compute metrics in fewer queries
        execution_ids = [str(e.id) for e in paginated_executions]
        metrics_map = {
            eid: self._aggregate_execution_metrics(eid) for eid in execution_ids
        }

        output = ExecutionMetricsSerializer(
            paginated_executions, many=True, context={"metrics_map": metrics_map}
        ).data

        paginated_response = paginator.get_paginated_response(output)
        envelope = paginated_response.data

        return self._gm.success_response(
            {
                "total_pages": envelope.get("total_pages", 1),
                "current_page": envelope.get("current_page", 1),
                "count": envelope.get("count", 0),
                "results": output,
            }
        )

    def _aggregate_execution_metrics(self, execution_id):
        query, params = get_kpi_metrics_query(execution_id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()

        if not row:
            return {}

        m = dict(zip(columns, row, strict=False))

        latency_percentiles = _compute_latency_percentiles_sql(execution_id)

        return {
            "latency": {
                "avg_agent_latency_ms": float(m.get("avg_agent_latency") or 0),
                "avg_response_time_ms": float(m.get("avg_response") or 0),
                "percentiles": latency_percentiles,
            },
            "cost": {
                "total_duration_seconds": int(m.get("total_duration") or 0),
            },
            "conversation": {
                "avg_user_wpm": float(m.get("avg_user_wpm") or 0),
                "avg_bot_wpm": float(m.get("avg_bot_wpm") or 0),
                "avg_talk_ratio": float(m.get("avg_talk_ratio") or 0),
                "avg_user_interruption_rate": float(
                    m.get("avg_user_interruption_rate") or 0
                ),
                "avg_ai_interruption_rate": float(
                    m.get("avg_ai_interruption_rate") or 0
                ),
                "avg_stop_time_after_interruption_ms": float(
                    m.get("avg_stop_time") or 0
                ),
            },
            "chat": {
                "avg_total_tokens": float(m.get("avg_total_tokens") or 0),
                "avg_input_tokens": float(m.get("avg_input_tokens") or 0),
                "avg_output_tokens": float(m.get("avg_output_tokens") or 0),
                "avg_chat_latency_ms": float(m.get("avg_chat_latency_ms") or 0),
                "avg_turn_count": float(m.get("avg_turn_count") or 0),
                "avg_csat_score": float(m.get("avg_csat_score") or 0),
            },
            "calls": {
                "total": int(m.get("total_calls") or 0),
                "completed": int(m.get("completed_calls") or 0),
                "failed": int(m.get("failed_calls") or 0),
                "pending": int(m.get("pending_calls") or 0),
            },
        }


class SimulationRunsView(APIView):
    """
    GET /simulation/runs/

    Run-level records with eval scores, scenario metadata, call details.

    Params:
    - run_test_name: paginated list of executions with eval scores
    - execution_id: one execution with paginated call results
    - call_execution_id: one call's full detail
    - eval_name: comma-separated eval filter
    - summary: include FMA explanation summary
    """

    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)

    @validated_request(
        query_serializer=SimulationRunsQuerySerializer,
        responses={
            200: SDKSimulationRunsResponseSerializer,
            400: SDKErrorResponseSerializer,
            404: SDKErrorResponseSerializer,
            500: SDKErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=sdk_validation_error_response,
        framework_query_params=("page", "limit"),
    )
    def get(self, request, *args, **kwargs):
        try:
            organization = getattr(request, "organization", None) or getattr(
                request.user, "organization", None
            )
            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            data = request.validated_query_data
            run_test_name = data.get("run_test_name")
            execution_id = data.get("execution_id")
            call_execution_id = data.get("call_execution_id")
            eval_names = data.get("eval_name")
            include_summary = data.get("summary", False)

            if call_execution_id:
                return self._handle_call_execution(
                    call_execution_id, organization, eval_names
                )

            if execution_id:
                return self._handle_execution_detail(
                    request, execution_id, organization, eval_names, include_summary
                )

            return self._handle_run_test_executions(
                request, run_test_name, organization, eval_names
            )

        except Exception:
            logger.exception("analytics_runs_error")
            return self._gm.internal_server_error_response(
                get_error_message("ANALYTICS_RUNS_ERROR")
            )

    def _handle_call_execution(self, call_execution_id, organization, eval_names):
        try:
            call = CallExecution.objects.select_related(
                "test_execution__run_test", "scenario"
            ).get(
                id=call_execution_id,
                test_execution__run_test__organization=organization,
            )
        except CallExecution.DoesNotExist:
            return self._gm.not_found(
                get_error_message("ANALYTICS_CALL_EXECUTION_NOT_FOUND")
            )

        output = CallRunDetailSerializer(call, context={"eval_names": eval_names}).data
        return self._gm.success_response(output)

    def _handle_execution_detail(
        self, request, execution_id, organization, eval_names, include_summary
    ):
        try:
            execution = TestExecution.objects.select_related("run_test").get(
                id=execution_id, run_test__organization=organization
            )
        except TestExecution.DoesNotExist:
            return self._gm.not_found(
                get_error_message("ANALYTICS_EXECUTION_NOT_FOUND")
            )

        run_test = execution.run_test

        # Compute eval summary
        eval_configs = _get_eval_configs_with_template(run_test)
        if eval_names:
            eval_configs = eval_configs.filter(name__in=eval_names)

        completed_calls = _get_completed_call_executions(run_test, execution_id)
        template_stats = _build_template_statistics(eval_configs, completed_calls)
        eval_results = _calculate_final_template_summaries(template_stats)

        # Paginate call results
        call_executions = (
            CallExecution.objects.filter(test_execution=execution)
            .select_related("scenario")
            .order_by("-updated_at")
        )

        paginator = ExtendedPageNumberPagination()
        paginated_calls = paginator.paginate_queryset(call_executions, request)

        call_results_output = CallRunSummarySerializer(
            paginated_calls, many=True, context={"eval_names": eval_names}
        ).data

        paginated_response = paginator.get_paginated_response(call_results_output)
        call_results_envelope = paginated_response.data

        result = ExecutionRunsSerializer(
            execution,
            context={"eval_results_map": {str(execution.id): eval_results}},
        ).data

        result["call_results"] = {
            "total_pages": call_results_envelope.get("total_pages", 1),
            "current_page": call_results_envelope.get("current_page", 1),
            "count": call_results_envelope.get("count", 0),
            "results": call_results_output,
        }

        if include_summary:
            result["eval_explanation_summary"] = execution.eval_explanation_summary
            result["eval_explanation_summary_status"] = (
                execution.eval_explanation_summary_status
            )

        return self._gm.success_response(result)

    def _handle_run_test_executions(
        self, request, run_test_name, organization, eval_names
    ):
        run_tests = RunTest.objects.filter(
            name=run_test_name, organization=organization
        )
        if not run_tests.exists():
            return self._gm.not_found(get_error_message("ANALYTICS_RUN_TEST_NOT_FOUND"))

        executions = (
            TestExecution.objects.filter(run_test__in=run_tests)
            .select_related("run_test")
            .order_by("-created_at")
        )

        paginator = ExtendedPageNumberPagination()
        paginated_executions = paginator.paginate_queryset(executions, request)

        # Pre-compute eval results for all executions in the page
        eval_results_map = {}
        for execution in paginated_executions:
            run_test = execution.run_test
            eval_configs = _get_eval_configs_with_template(run_test)
            if eval_names:
                eval_configs = eval_configs.filter(name__in=eval_names)

            completed_calls = _get_completed_call_executions(
                run_test, str(execution.id)
            )
            template_stats = _build_template_statistics(eval_configs, completed_calls)
            eval_results_map[str(execution.id)] = _calculate_final_template_summaries(
                template_stats
            )

        output = ExecutionRunsSerializer(
            paginated_executions,
            many=True,
            context={"eval_results_map": eval_results_map},
        ).data

        paginated_response = paginator.get_paginated_response(output)
        envelope = paginated_response.data

        return self._gm.success_response(
            {
                "total_pages": envelope.get("total_pages", 1),
                "current_page": envelope.get("current_page", 1),
                "count": envelope.get("count", 0),
                "results": output,
            }
        )


class SimulationAnalyticsView(APIView):
    """
    GET /simulation/analytics/

    Aggregated analytics view: eval scores (radar chart data), critical issues,
    FMA suggestions. Corresponds to the Analytics tab in the UI.

    Params:
    - run_test_name: analytics across all executions for a run test
    - execution_id: analytics for one execution
    - eval_name: comma-separated eval filter
    - summary: include FMA explanation summary (default: true for this endpoint)
    """

    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)

    @validated_request(
        query_serializer=SimulationAnalyticsQuerySerializer,
        responses={
            200: SDKSimulationAnalyticsResponseSerializer,
            400: SDKErrorResponseSerializer,
            404: SDKErrorResponseSerializer,
            500: SDKErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=sdk_validation_error_response,
    )
    def get(self, request, *args, **kwargs):
        try:
            organization = getattr(request, "organization", None) or getattr(
                request.user, "organization", None
            )
            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            data = request.validated_query_data
            run_test_name = data.get("run_test_name")
            execution_id = data.get("execution_id")
            eval_names = data.get("eval_name")
            include_summary = data.get("summary", True)

            if execution_id:
                return self._handle_execution(
                    execution_id, organization, eval_names, include_summary
                )

            return self._handle_run_test(
                run_test_name, organization, eval_names, include_summary
            )

        except Exception:
            logger.exception("analytics_error")
            return self._gm.internal_server_error_response(
                get_error_message("ANALYTICS_ERROR")
            )

    def _handle_execution(
        self, execution_id, organization, eval_names, include_summary
    ):
        try:
            execution = TestExecution.objects.select_related("run_test").get(
                id=execution_id, run_test__organization=organization
            )
        except TestExecution.DoesNotExist:
            return self._gm.not_found(
                get_error_message("ANALYTICS_EXECUTION_NOT_FOUND")
            )

        return self._build_analytics_response(execution, eval_names, include_summary)

    def _handle_run_test(
        self, run_test_name, organization, eval_names, include_summary
    ):
        run_tests = RunTest.objects.filter(
            name=run_test_name, organization=organization
        )
        if not run_tests.exists():
            return self._gm.not_found(get_error_message("ANALYTICS_RUN_TEST_NOT_FOUND"))

        latest_execution = (
            TestExecution.objects.filter(run_test__in=run_tests, status="completed")
            .select_related("run_test")
            .order_by("-completed_at")
            .first()
        )

        if not latest_execution:
            return self._gm.success_response(
                {
                    "run_test_name": run_test_name,
                    "message": "No completed executions found.",
                    "eval_results": [],
                    "eval_averages": {},
                    "system_summary": {},
                }
            )

        return self._build_analytics_response(
            latest_execution, eval_names, include_summary
        )

    def _build_analytics_response(self, execution, eval_names, include_summary):
        """Build the analytics response for a given execution instance."""
        run_test = execution.run_test
        execution_id_str = str(execution.id)

        # Eval summary (radar chart data)
        eval_configs = _get_eval_configs_with_template(run_test)
        if eval_names:
            eval_configs = eval_configs.filter(name__in=eval_names)

        completed_calls = _get_completed_call_executions(run_test, execution_id_str)
        template_stats = _build_template_statistics(eval_configs, completed_calls)
        eval_results = _calculate_final_template_summaries(template_stats)

        # Aggregated eval averages from SQL
        eval_query, eval_params = get_kpi_eval_metrics_query(execution_id_str)
        with connection.cursor() as cursor:
            cursor.execute(eval_query, eval_params)
            eval_rows = cursor.fetchall()

        eval_averages = self._build_eval_averages(eval_rows)

        # System metrics summary
        query, params = get_kpi_metrics_query(execution_id_str)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()

        system_summary = {}
        if row:
            m = dict(zip(columns, row, strict=False))
            system_summary = {
                "total_calls": int(m.get("total_calls") or 0),
                "completed_calls": int(m.get("completed_calls") or 0),
                "failed_calls": int(m.get("failed_calls") or 0),
                "avg_score": float(m.get("avg_score") or 0),
                "avg_response_time_ms": float(m.get("avg_response") or 0),
                "total_duration_seconds": int(m.get("total_duration") or 0),
            }

        result = {
            "execution_id": execution_id_str,
            "run_test_name": run_test.name,
            "status": execution.status,
            "eval_results": eval_results,
            "eval_averages": eval_averages,
            "system_summary": system_summary,
        }

        if include_summary:
            result["eval_explanation_summary"] = execution.eval_explanation_summary
            result["eval_explanation_summary_status"] = (
                execution.eval_explanation_summary_status
            )

        output = AnalyticsResponseSerializer(result).data
        return self._gm.success_response(output)

    def _build_eval_averages(self, eval_rows):
        eval_averages = {}
        for (
            _metric_id,
            metric_name,
            output_type,
            avg_value,
            choice_value,
            choice_count,
        ) in eval_rows:
            if output_type in ("Pass/Fail", "score"):
                field_name = f"avg_{metric_name.lower().replace(' ', '_')}"
                eval_averages[field_name] = float(avg_value) if avg_value else 0
            elif output_type == "choices" and choice_value is not None:
                base_name = metric_name.lower().replace(" ", "_")
                if base_name not in eval_averages:
                    eval_averages[base_name] = {}
                eval_averages[base_name][choice_value.lower()] = choice_count
            elif output_type == "choices" and avg_value is not None:
                field_name = f"avg_{metric_name.lower().replace(' ', '_')}"
                eval_averages[field_name] = float(avg_value)
        return eval_averages
