import copy
import csv
import json
import math
import os
import re
import traceback
from urllib.parse import urlencode

import structlog
from django.db import connection, models, transaction
from django.db.models import Avg, Count, Max, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.utils.test_execution import (
    DEFAULT_CHAT_SIM_COL,
    DEFAULT_VOICE_SIM_COL,
    LEGACY_SIM_COLUMN_ID_MAP,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.replay_session import ReplaySession, ReplaySessionStep
from tracer.models.trace import Trace

logger = structlog.get_logger(__name__)
from model_hub.models.api_key import ApiKey
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import (
    normalize_eval_runtime_config,
    params_with_defaults_for_response,
)
from simulate.models import (
    AgentDefinition,
    CallExecution,
    CallLogEntry,
    ChatMessageModel,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    SimulatorAgent,
    TestExecution,
)
from simulate.models.agent_version import AgentVersion
from simulate.models.run_test import CreateCallExecution
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.test_execution import (
    CallExecutionSnapshot,
    EvalExplanationSummaryStatus,
)
from simulate.serializers.run_test import (
    CreateRunTestSerializer,
    RunTestSerializer,
    UpdateRunTestSerializer,
)
from simulate.serializers.test_execution import (
    CallExecutionDetailSerializer,
    CallExecutionRerunSerializer,
    CallExecutionSerializer,
    CallExecutionSnapshotSerializer,
    PerformanceSummarySerializer,
    RunNewEvalsOnTestExecutionSerializer,
    TestExecutionAnalyticsSerializer,
    TestExecutionBulkDeleteSerializer,
    TestExecutionColumnOrderSerializer,
    TestExecutionRerunSerializer,
    TestExecutionSerializer,
)

# Import Temporal activities (using @temporal_activity drop-in decorator)
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
    run_new_evals_on_call_executions_task,
)
from simulate.tasks.eval_summary_tasks import run_eval_summary_task
from simulate.utils.agent_optimiser import (
    create_optimiser_run_for_test_execution,
    get_latest_optimiser_result,
    get_or_create_optimiser_for_test_execution,
)
from simulate.utils.eval_summary import (
    _build_template_statistics,
    _calculate_final_template_summaries,
    _get_completed_call_executions,
    _get_eval_configs_with_template,
)
from simulate.utils.sql_query import (
    get_combined_call_executions_and_snapshots_count_query,
    get_combined_call_executions_and_snapshots_query,
    get_kpi_eval_metrics_query,
    get_kpi_metrics_query,
)
from simulate.utils.test_execution_utils import TestExecutionUtils
from tfc.ee_gates import strip_turing_from_config_options
from tfc.settings import settings as app_settings
from tfc.settings.settings import VAPI_INDIAN_PHONE_NUMBER_ID
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


def _voice_sim_gate_response(user_organization, gm):
    """Return a Response blocking voice simulation if it's not available in
    this deployment for this org, else None.

    Two layers:
      1. OSS gate (402, upgrade_required) — via tfc.ee_gates.
      2. Cloud/EE plan entitlement (`has_voice_sim`) — 403 on denial.
    """
    from tfc.ee_gates import voice_sim_oss_gate_response

    oss_gate = voice_sim_oss_gate_response()
    if oss_gate is not None:
        return oss_gate

    try:
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        # ee.usage.deployment exists but entitlements is missing — partial
        # EE install. Fail closed.
        return Response(
            {
                "error": (
                    "Voice simulation is not available on this deployment. "
                    "Upgrade to cloud or enterprise to run voice calls."
                ),
                "upgrade_required": True,
                "feature": "voice_sim",
            },
            status=402,
        )

    feat_check = Entitlements.check_feature(
        str(user_organization.id), "has_voice_sim"
    )
    if not feat_check.allowed:
        return gm.forbidden_response(feat_check.reason)
    return None


class RunTestListView(APIView):
    """
    API View to list run tests for an organization with pagination and search
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, *args, **kwargs):
        """
        Get paginated list of run tests for the user's organization
        Query Parameters:
        - search: search string to filter run tests by name
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        - simulation_type: filter by source type (RunTest.SourceTypes values:
            'agent_definition' or 'prompt')
        - prompt_template_id: filter by prompt template ID (used when
            simulation_type is 'prompt')
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get query parameters
            search_query = request.query_params.get("search", "").strip()
            simulation_type = request.query_params.get("simulation_type", "").strip()
            prompt_template_id = request.query_params.get(
                "prompt_template_id", ""
            ).strip()

            # Filter run tests by organization (only non-deleted)
            # Prefetch simulate_eval_configs to avoid N+1 in serializer's to_representation
            # Prefetch agent_definition__versions for latest_version lookup (ordered by version_number desc)
            latest_version_prefetch = Prefetch(
                "agent_definition__versions",
                queryset=AgentVersion.objects.order_by("-version_number"),
                to_attr="_prefetched_versions",
            )
            run_tests = (
                RunTest.objects.filter(organization=user_organization, deleted=False)
                .prefetch_related(
                    "scenarios", "simulate_eval_configs", latest_version_prefetch
                )
                .select_related(
                    "agent_definition",
                    "agent_definition__credentials",
                    "agent_version",
                    "simulator_agent",
                    "prompt_template",
                    "prompt_version",
                )
            )

            # Apply simulation_type filter using RunTest.SourceTypes enum
            if simulation_type == RunTest.SourceTypes.PROMPT:
                run_tests = run_tests.filter(source_type=RunTest.SourceTypes.PROMPT)
                # Filter by prompt_template_id if provided
                if prompt_template_id:
                    run_tests = run_tests.filter(prompt_template_id=prompt_template_id)
            elif simulation_type == RunTest.SourceTypes.AGENT_DEFINITION:
                run_tests = run_tests.filter(
                    source_type=RunTest.SourceTypes.AGENT_DEFINITION
                )

            # Apply search filter if search query is provided
            if search_query:
                # Create case-insensitive regex pattern for search
                pattern = rf"(?i){re.escape(search_query)}"
                run_tests = run_tests.filter(
                    models.Q(name__regex=pattern)
                    | models.Q(agent_definition__agent_name__regex=pattern)
                )

            # Annotate with the most recent execution's created_at
            run_tests = run_tests.annotate(last_run_at=Max("executions__created_at"))

            # Order by creation date (newest first)
            run_tests = run_tests.order_by("-created_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(run_tests, request)

            # Serialize the data
            serializer = RunTestSerializer(result_page, many=True)

            # Return paginated response
            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve run tests: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateRunTestView(APIView):
    """
    API View to create a new RunTest
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, *args, **kwargs):
        """Create a new RunTest"""
        try:
            # Validate request data
            serializer = CreateRunTestSerializer(
                data=request.data, context={"request": request}
            )
            if not serializer.is_valid():
                return self.gm.bad_request("Invalid data")

            validated_data = serializer.validated_data

            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Resolve the agent definition up-front so we can gate on its
            # type. Only voice simulations are entitlement-gated; chat
            # (text) simulations are available on every plan.
            agent_definition = AgentDefinition.objects.get(
                id=validated_data["agent_definition_id"],
                organization=user_organization,
            )

            if (
                agent_definition.agent_type
                == AgentDefinition.AgentTypeChoices.VOICE
            ):
                forbidden = _voice_sim_gate_response(
                    user_organization, self.gm
                )
                if forbidden is not None:
                    return forbidden

            # Create the RunTest
            with transaction.atomic():
                agent_version = request.data.get("agent_version")
                if agent_version:
                    agent_version = AgentVersion.objects.get(
                        id=agent_version, deleted=False, organization=user_organization
                    )

                # simulator_agent = SimulatorAgent.objects.get(
                #     id=validated_data['simulator_agent_id'],
                #     organization=user_organization
                # )

                run_test = RunTest.objects.create(
                    name=validated_data["name"],
                    description=validated_data.get("description", ""),
                    agent_definition=agent_definition,
                    agent_version=agent_version,
                    simulator_agent=None,
                    dataset_row_ids=validated_data.get("dataset_row_ids", []),
                    organization=user_organization,
                    enable_tool_evaluation=validated_data.get(
                        "enable_tool_evaluation", False
                    ),
                )

                # Add scenarios
                scenarios = Scenarios.objects.filter(
                    id__in=validated_data["scenario_ids"],
                    organization=user_organization,
                )
                run_test.scenarios.set(scenarios)

                # Handle evaluations - create SimulateEvalConfig instances
                evaluations_config = validated_data.get("evaluations_config", [])
                eval_config_ids = validated_data.get("eval_config_ids", [])

                # Create SimulateEvalConfig instances from evaluations_config
                if evaluations_config:
                    for eval_config_data in evaluations_config:
                        # Get EvalTemplate by ID if template_id is provided (converted from templateId by middleware)
                        template_id = eval_config_data.get("template_id")

                        if template_id:
                            try:
                                eval_template = EvalTemplate.no_workspace_objects.get(
                                    Q(organization=user_organization)
                                    | Q(organization__isnull=True),
                                    id=template_id,
                                )

                                # Create SimulateEvalConfig
                                SimulateEvalConfig.objects.create(
                                    eval_template=eval_template,
                                    name=eval_config_data.get(
                                        "name", f"Eval-{template_id}"
                                    ),
                                    config=normalize_eval_runtime_config(
                                        eval_template.config,
                                        eval_config_data.get("config", {}),
                                    ),
                                    mapping=eval_config_data.get("mapping", {}),
                                    run_test=run_test,
                                    filters=eval_config_data.get("filters", {}),
                                    error_localizer=eval_config_data.get(
                                        "error_localizer", False
                                    ),
                                    model=eval_config_data.get("model", None),
                                    eval_group_id=eval_config_data.get(
                                        "eval_group", None
                                    ),
                                )
                            except EvalTemplate.DoesNotExist:
                                # Skip if template doesn't exist
                                continue

                # Add existing evaluation configs if provided
                if eval_config_ids:
                    SimulateEvalConfig.objects.filter(
                        id__in=eval_config_ids, run_test__organization=user_organization
                    )

                replay_session_id = validated_data.get("replay_session_id")
                if replay_session_id:
                    replay_session = ReplaySession.objects.get(
                        id=replay_session_id,
                        project__organization=user_organization,
                    )
                    replay_session.current_step = ReplaySessionStep.COMPLETED
                    replay_session.run_test = run_test
                    replay_session.save(
                        update_fields=["current_step", "run_test", "updated_at"]
                    )

                # Serialize and return the created run test
                response_serializer = RunTestSerializer(run_test)
                return Response(
                    response_serializer.data, status=status.HTTP_201_CREATED
                )
        except ReplaySession.DoesNotExist:
            return self.gm.not_found(get_error_message("REPLAY_SESSION_NOT_FOUND"))

        except Exception as e:
            return Response(
                {"error": f"Failed to create run test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestDetailView(APIView):
    """
    API View to retrieve, update, or delete a specific RunTest
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, run_test_id, *args, **kwargs):
        """Retrieve a specific RunTest"""
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            serializer = RunTestSerializer(run_test)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except RunTest.DoesNotExist:
            return Response(
                {"error": "Run test not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve run test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def patch(self, request, run_test_id, *args, **kwargs):
        """Update a specific RunTest"""
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Validate request data
            serializer = UpdateRunTestSerializer(
                data=request.data, context={"request": request}
            )

            if not serializer.is_valid():
                return self.gm.bad_request("Invalid data")

            validated_data = serializer.validated_data

            # Update fields if provided
            with transaction.atomic():
                if "name" in validated_data:
                    run_test.name = validated_data["name"]

                if "description" in validated_data:
                    run_test.description = validated_data["description"]

                if "agent_definition_id" in validated_data:
                    agent_definition = AgentDefinition.objects.get(
                        id=validated_data["agent_definition_id"],
                        organization=user_organization,
                    )
                    run_test.agent_definition = agent_definition

                if "simulator_agent_id" in validated_data:
                    simulator_agent = SimulatorAgent.objects.get(
                        id=validated_data["simulator_agent_id"],
                        organization=user_organization,
                    )
                    run_test.simulator_agent = simulator_agent

                if "dataset_row_ids" in validated_data:
                    run_test.dataset_row_ids = validated_data["dataset_row_ids"]

                if "eval_config_ids" in validated_data:
                    eval_configs = SimulateEvalConfig.objects.filter(
                        id__in=validated_data["eval_config_ids"],
                        run_test__organization=user_organization,
                    )
                    # Update the run_test field for existing eval configs
                    for eval_config in eval_configs:
                        eval_config.run_test = run_test
                        eval_config.save()

                if "scenario_ids" in validated_data:
                    scenarios = Scenarios.objects.filter(
                        id__in=validated_data["scenario_ids"],
                        organization=user_organization,
                    )
                    run_test.scenarios.set(scenarios)

                run_test.save()

                # Serialize and return the updated run test
                response_serializer = RunTestSerializer(run_test)
                return Response(response_serializer.data, status=status.HTTP_200_OK)

        except RunTest.DoesNotExist:
            return Response(
                {"error": "Run test not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to update run test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request, run_test_id, *args, **kwargs):
        """Delete a specific RunTest (soft delete)"""
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Soft delete the run test
            run_test.delete()  # This calls the custom delete method that sets deleted=True

            return Response(
                {"message": "Run test deleted successfully"}, status=status.HTTP_200_OK
            )

        except RunTest.DoesNotExist:
            return Response(
                {"error": "Run test not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to delete run test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestExecutionView(APIView):
    """
    API View to execute a test run with all its scenarios
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()
        self.test_executor = TestExecutor()

    def post(self, request, run_test_id, *args, **kwargs):
        """Execute a test run"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            if (
                run_test.agent_definition
                and run_test.agent_definition.agent_type
                == AgentDefinition.AgentTypeChoices.VOICE
            ):
                forbidden = _voice_sim_gate_response(
                    user_organization, self.gm
                )
                if forbidden is not None:
                    return forbidden

            # Get parameters from request
            scenario_ids = request.data.get("scenario_ids", [])
            simulator_id = request.data.get("simulator_id", None)
            # select_all = request.data.get("select_all", False)

            # Get all available scenario IDs for this run test
            run_test_scenario_ids = list(
                run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
            )

            if scenario_ids:
                final_scenario_ids = scenario_ids
            else:
                final_scenario_ids = [
                    str(scenario_id) for scenario_id in run_test_scenario_ids
                ]

            # Validate that at least one scenario is available for execution
            if not final_scenario_ids:
                return Response(
                    {"error": "At least one scenario is required to execute the test."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check if Temporal test execution is enabled
            if getattr(app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", False):
                result = self._execute_with_temporal(
                    run_test=run_test,
                    scenario_ids=final_scenario_ids,
                    simulator_id=simulator_id,
                )
            else:
                # Execute the test using the legacy test executor (Celery)
                result = self.test_executor.execute_test(
                    run_test_id=str(run_test.id),
                    user_id=str(request.user.id),
                    scenario_ids=final_scenario_ids,
                    simulator_id=simulator_id,
                )

            if result["success"]:
                return Response(
                    {
                        "message": "Test execution started successfully",
                        "execution_id": result["execution_id"],
                        "run_test_id": result["run_test_id"],
                        "status": result["status"],
                        "total_scenarios": result["total_scenarios"],
                        "total_calls": result.get("total_calls", 0),
                        "scenario_ids": [
                            str(scenario_id) for scenario_id in final_scenario_ids
                        ],
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return self.gm.bad_request(result["error"])

        except Exception as e:
            return Response(
                {"error": f"Failed to execute test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _execute_with_temporal(
        self, run_test: RunTest, scenario_ids: list[str], simulator_id: str | None
    ) -> dict:
        """
        Execute test using Temporal workflow.

        Creates TestExecution record and starts TestExecutionWorkflow.
        The workflow handles all orchestration (setup, call creation, execution).
        """
        from simulate.temporal.client import start_test_execution_workflow

        try:
            # Get simulator agent if provided
            simulator_agent = None
            if simulator_id:
                try:
                    simulator_agent = SimulatorAgent.objects.get(id=simulator_id)
                except SimulatorAgent.DoesNotExist:
                    simulator_agent = run_test.simulator_agent
            else:
                simulator_agent = run_test.simulator_agent

            # Create TestExecution record
            test_execution = TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.PENDING,
                started_at=timezone.now(),
                total_scenarios=len(scenario_ids),
                scenario_ids=[str(sid) for sid in scenario_ids],
                picked_up_by_executor=False,
                simulator_agent=simulator_agent,
                agent_definition=run_test.agent_definition,
                agent_version=run_test.agent_version,
            )

            # Start Temporal workflow
            workflow_id = start_test_execution_workflow(
                test_execution_id=str(test_execution.id),
                run_test_id=str(run_test.id),
                org_id=str(run_test.organization_id),
                scenario_ids=scenario_ids,
                simulator_id=str(simulator_id) if simulator_id else None,
            )

            logger.info(
                f"Started Temporal workflow {workflow_id} for test execution {test_execution.id}"
            )

            return {
                "success": True,
                "run_test_id": str(run_test.id),
                "execution_id": str(test_execution.id),
                "workflow_id": workflow_id,
                "status": "started",
                "total_scenarios": len(scenario_ids),
                "total_calls": 0,  # Will be set by workflow after setup
            }

        except Exception as e:
            logger.exception(f"Failed to start Temporal workflow: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to start test execution: {str(e)}",
                "run_test_id": str(run_test.id),
            }


class TestExecutionStatusView(APIView):
    """
    API View to get test execution status
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, run_test_id, *args, **kwargs):
        """Get test execution status"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )
            test_executor = TestExecutor()

            # Get test execution status
            result = test_executor.get_test_status(run_test_id)

            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Failed to get test status: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestExecutionCancelView(APIView):
    """
    API View to cancel a test execution
    Supports cancelling by run_test_id (latest execution) or specific test_execution_id
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, run_test_id=None, test_execution_id=None, *args, **kwargs):
        """Cancel a test execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Verify access to the test
            if test_execution_id:
                # Verify user has access to this test execution
                test_execution = get_object_or_404(
                    TestExecution,
                    id=test_execution_id,
                    run_test__organization=user_organization,
                    run_test__deleted=False,
                )
                run_test_id = str(test_execution.run_test_id)
            elif run_test_id:
                # Verify user has access to this run test
                get_object_or_404(
                    RunTest,
                    id=run_test_id,
                    organization=user_organization,
                    deleted=False,
                )
                test_execution_id = None
            else:
                return self.gm.bad_request(
                    "Either run_test_id or test_execution_id must be provided"
                )

            test_execution.status = TestExecution.ExecutionStatus.CANCELLING
            test_execution.save()

            # Check if Temporal test execution is enabled
            if getattr(app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", False):
                result = self._cancel_with_temporal(test_execution)
            else:
                # Cancel using legacy test executor (Celery)
                test_executor = TestExecutor()
                result = test_executor.cancel_test(
                    run_test_id=run_test_id, test_execution_id=test_execution_id
                )

            if result["success"]:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return self.gm.bad_request(result.get("error", "Failed to cancel test"))

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to cancel test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _cancel_with_temporal(self, test_execution) -> dict:
        """Cancel test execution via Temporal workflow, with DB fallback.

        Tries to cancel both the original TestExecutionWorkflow (fresh runs)
        and any active RerunCoordinatorWorkflow (reruns).
        """
        from simulate.temporal.client import (
            cancel_test_execution,
            cancel_workflow,
        )

        test_execution_id = str(test_execution.id)
        any_cancelled = False

        try:
            # Try cancelling the original TestExecutionWorkflow (fresh run)
            if cancel_test_execution(test_execution_id):
                any_cancelled = True

            # Try cancelling the active RerunCoordinatorWorkflow (rerun)
            active_rerun_wf_id = None
            if test_execution.execution_metadata:
                active_rerun_wf_id = test_execution.execution_metadata.get(
                    "active_rerun_workflow_id"
                )

            if active_rerun_wf_id and cancel_workflow(
                active_rerun_wf_id, cancel_signal="cancel"
            ):
                any_cancelled = True

            if any_cancelled:
                return {
                    "success": True,
                    "message": "Cancellation signal sent to workflow",
                    "test_execution_id": test_execution_id,
                }
            else:
                logger.warning(
                    f"No Temporal workflows found for {test_execution_id}, "
                    f"falling back to DB cancellation"
                )
                return self._cancel_via_db(test_execution_id)

        except Exception as e:
            logger.exception(f"Failed to cancel Temporal workflow: {str(e)}")
            return self._cancel_via_db(test_execution_id)

    def _cancel_via_db(self, test_execution_id: str) -> dict:
        """Fallback: cancel test execution directly in DB when Temporal is unavailable."""
        try:
            test_executor = TestExecutor()
            return test_executor.cancel_test(test_execution_id=test_execution_id)
        except Exception as e:
            logger.exception(f"DB fallback cancellation also failed: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to cancel test execution: {str(e)}",
                "test_execution_id": test_execution_id,
            }


class AllActiveTestsView(APIView):
    """
    API View to get all active tests
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """Get all active tests"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            test_executor = TestExecutor()

            # Get all active tests
            active_tests = test_executor.get_all_active_tests()

            return Response(
                {"active_tests": active_tests, "total_active": len(active_tests)},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to get active tests: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestAPIView(APIView):
    """
    API View to list run tests for an organization with pagination and search
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Get paginated list of run tests for the user's organization
        Query Parameters:
        - search: search string to filter run tests by name
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get search query parameter
            search_query = request.query_params.get("search", "").strip()

            # Filter run tests by organization (only non-deleted)
            run_tests = (
                RunTest.objects.filter(organization=user_organization, deleted=False)
                .prefetch_related("scenarios")
                .select_related(
                    "agent_definition",
                    "agent_definition__credentials",
                    "simulator_agent",
                )
            )

            # Apply search filter if search query is provided
            if search_query:
                # Create case-insensitive regex pattern for search
                pattern = rf"(?i){re.escape(search_query)}"
                run_tests = run_tests.filter(
                    models.Q(name__regex=pattern)
                    | models.Q(agent_definition__agent_name__regex=pattern)
                )

            # Order by creation date (newest first)
            run_tests = run_tests.order_by("-created_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(run_tests, request)

            # Serialize the data
            serializer = RunTestSerializer(result_page, many=True)

            # Return paginated response
            return paginator.get_paginated_response(serializer.data)

        except NotFound:
            raise
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve run tests: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestExecutionAPIView(APIView):
    """
    API View to list test executions for an organization with pagination and search
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Get paginated list of test executions for the user's organization
        Query Parameters:
        - search: search string to filter test executions by run test name
        - status: filter by execution status
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get query parameters
            search_query = request.query_params.get("search", "").strip()
            status_filter = request.query_params.get("status", "").strip()

            # Filter test executions by organization
            test_executions = TestExecution.objects.filter(
                run_test__organization=user_organization, run_test__deleted=False
            ).select_related("run_test", "run_test__agent_definition")

            # Apply search filter if search query is provided
            if search_query:
                pattern = rf"(?i){re.escape(search_query)}"
                test_executions = test_executions.filter(
                    models.Q(run_test__name__regex=pattern)
                    | models.Q(run_test__agent_definition__agent_name__regex=pattern)
                )

            # Apply status filter if provided
            if status_filter:
                test_executions = test_executions.filter(status=status_filter)

            # Order by creation date (newest first)
            test_executions = test_executions.order_by("-created_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(test_executions, request)

            # Serialize the data
            serializer = TestExecutionSerializer(result_page, many=True)

            # Return paginated response
            return paginator.get_paginated_response(serializer.data)

        except NotFound:
            raise
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve test executions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CallExecutionAPIView(APIView):
    """
    API View to list call executions for an organization with pagination and search
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Get paginated list of call executions for the user's organization
        Query Parameters:
        - search: search string to filter call executions by phone number or scenario name
        - status: filter by call status
        - test_execution_id: filter by specific test execution
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get query parameters
            search_query = request.query_params.get("search", "").strip()
            status_filter = request.query_params.get("status", "").strip()
            test_execution_id = request.query_params.get(
                "test_execution_id", ""
            ).strip()

            # Filter call executions by organization
            call_executions = CallExecution.objects.filter(
                test_execution__run_test__organization=user_organization,
                test_execution__run_test__deleted=False,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
            ).select_related("test_execution", "test_execution__run_test", "scenario")

            # Apply search filter if search query is provided
            if search_query:
                pattern = rf"(?i){re.escape(search_query)}"
                call_executions = call_executions.filter(
                    models.Q(phone_number__regex=pattern)
                    | models.Q(scenario__name__regex=pattern)
                )

            # Apply status filter if provided
            if status_filter:
                call_executions = call_executions.filter(status=status_filter)

            # Apply test execution filter if provided
            if test_execution_id:
                call_executions = call_executions.filter(
                    test_execution_id=test_execution_id
                )

            # Order by updated date (newest/most recently rerun first)
            call_executions = call_executions.order_by("-updated_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(call_executions, request)

            # Serialize the data
            serializer = CallExecutionSerializer(result_page, many=True)

            # Return paginated response
            return paginator.get_paginated_response(serializer.data)

        except NotFound:
            raise
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve call executions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestKPIsView(APIView):
    """
    API View to get combined KPI values for a specific run test
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, test_execution_id, *args, **kwargs):
        """
        Get combined KPI values for a specific run test
        Returns: Total Calls, Success Rate, Avg Score, Avg Response, Avg Accuracy, Avg Sentiment
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            test_executor = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
                run_test__deleted=False,
            )

            # Get agent type from the test execution's historical data
            run_test = test_executor.run_test
            agent_version = test_executor.agent_version
            agent_definition = test_executor.agent_definition

            is_inbound = None
            if agent_version:
                # Use snapshot for historical agent type
                snapshot = agent_version.configuration_snapshot or {}
                agent_type = (
                    snapshot.get("agent_type")
                    or (
                        agent_version.agent_definition.agent_type
                        if agent_version.agent_definition
                        else None
                    )
                    or (agent_definition.agent_type if agent_definition else None)
                    or AgentDefinition.AgentTypeChoices.VOICE
                )
                is_inbound = snapshot.get("inbound")
            elif agent_definition:
                agent_type = agent_definition.agent_type
                is_inbound = agent_definition.inbound
            elif run_test.agent_definition:
                # Fallback to run_test's agent_definition for legacy executions
                agent_type = run_test.agent_definition.agent_type
                is_inbound = run_test.agent_definition.inbound
            else:
                # Prompt-based simulations are always TEXT/chat type
                agent_type = AgentDefinition.AgentTypeChoices.TEXT
            is_chat = agent_type == AgentDefinition.AgentTypeChoices.TEXT

            # --- Single SQL query for all counts + metric averages ---
            kpi_query, kpi_params = get_kpi_metrics_query(test_execution_id)
            with connection.cursor() as cursor:
                cursor.execute(kpi_query, kpi_params)
                columns = [col[0] for col in cursor.description]
                row = cursor.fetchone()
            metrics = dict(zip(columns, row)) if row else {}

            total_calls = metrics.get("total_calls", 0) or 0
            pending_calls = metrics.get("pending_calls", 0) or 0
            queued_calls = metrics.get("queued_calls", 0) or 0
            failed_calls = metrics.get("failed_calls", 0) or 0
            calls_attempted = total_calls - pending_calls - queued_calls

            if is_chat:
                connected_calls = metrics.get("completed_calls", 0) or 0
                calls_connected_percentage = (
                    round((connected_calls / total_calls * 100), 1)
                    if total_calls > 0
                    else 0
                )
            else:
                connected_calls = metrics.get("connected_voice_calls", 0) or 0
                calls_connected_percentage = (
                    round((connected_calls / calls_attempted * 100), 2)
                    if calls_attempted > 0
                    else 0.0
                )

            total_duration = metrics.get("total_duration", 0) or 0
            avg_score = float(metrics.get("avg_score") or 0)
            avg_response = float(metrics.get("avg_response") or 0)

            # Voice metrics
            avg_agent_latency = float(metrics.get("avg_agent_latency") or 0)
            avg_user_interruption_count = float(
                metrics.get("avg_user_interruption_count") or 0
            )
            avg_user_interruption_rate = float(
                metrics.get("avg_user_interruption_rate") or 0
            )
            avg_user_wpm = float(metrics.get("avg_user_wpm") or 0)
            avg_bot_wpm = float(metrics.get("avg_bot_wpm") or 0)
            avg_talk_ratio = float(metrics.get("avg_talk_ratio") or 0)
            avg_ai_interruption_count = float(
                metrics.get("avg_ai_interruption_count") or 0
            )
            avg_ai_interruption_rate = float(
                metrics.get("avg_ai_interruption_rate") or 0
            )
            avg_stop_time_after_interruption = float(metrics.get("avg_stop_time") or 0)

            # Talk percentages from talk ratio
            if avg_talk_ratio > 0:
                agent_talk_percentage = round(
                    (avg_talk_ratio / (avg_talk_ratio + 1)) * 100, 1
                )
                customer_talk_percentage = round((1 / (avg_talk_ratio + 1)) * 100, 1)
            else:
                agent_talk_percentage = 0.0
                customer_talk_percentage = 0.0

            # Chat metrics
            avg_total_tokens = float(metrics.get("avg_total_tokens") or 0)
            avg_input_tokens = float(metrics.get("avg_input_tokens") or 0)
            avg_output_tokens = float(metrics.get("avg_output_tokens") or 0)
            avg_chat_latency_ms = float(metrics.get("avg_chat_latency_ms") or 0)
            avg_turn_count = float(metrics.get("avg_turn_count") or 0)
            avg_csat_score = float(metrics.get("avg_csat_score") or 0)

            # --- Eval metrics: aggregated in SQL via jsonb_each ---
            eval_query, eval_params = get_kpi_eval_metrics_query(test_execution_id)
            with connection.cursor() as cursor:
                cursor.execute(eval_query, eval_params)
                eval_rows = cursor.fetchall()

            # Assemble eval averages from SQL results
            eval_averages = {}
            choice_metric_ids = set()
            choice_counts = {}  # {metric_name: {choice_value: count}}

            for (
                metric_id,
                metric_name,
                output_type,
                avg_value,
                choice_value,
                choice_count,
            ) in eval_rows:
                if output_type in ("Pass/Fail", "score"):
                    field_name = f"avg_{metric_name.lower().replace(' ', '_')}"
                    eval_averages[field_name] = float(avg_value) if avg_value else 0
                elif output_type == "choices":
                    if choice_value is not None:
                        # String/array choice: accumulate counts
                        choice_metric_ids.add(metric_id)
                        base_name = metric_name.lower().replace(" ", "_")
                        if base_name not in choice_counts:
                            choice_counts[base_name] = {"_metric_id": metric_id}
                        choice_counts[base_name][choice_value.lower()] = choice_count
                    elif avg_value is not None:
                        # Numeric choice: already averaged
                        field_name = f"avg_{metric_name.lower().replace(' ', '_')}"
                        eval_averages[field_name] = float(avg_value)

            # Fetch choices config for choice-type eval metrics
            if choice_metric_ids:
                simulate_eval_configs = SimulateEvalConfig.objects.filter(
                    id__in=list(choice_metric_ids)
                )
                config_choices_map = {}
                for sec in simulate_eval_configs:
                    cfg = (sec.config or {}).get("config", {})
                    choices = cfg.get("choices", [])
                    if choices:
                        config_choices_map[str(sec.id)] = choices

                for base_name, data in choice_counts.items():
                    mid = data.pop("_metric_id")
                    eval_averages[base_name] = data
                    choices_list = config_choices_map.get(str(mid), [])
                    if choices_list:
                        eval_averages[base_name]["choices"] = choices_list

            # --- Scenario graphs ---
            scenario_graphs = {}
            unique_scenarios = (
                CallExecution.objects.filter(test_execution_id=test_execution_id)
                .values_list("scenario_id", flat=True)
                .distinct()
            )

            if unique_scenarios:
                latest_graphs = (
                    ScenarioGraph.objects.filter(
                        scenario_id__in=unique_scenarios, is_active=True
                    )
                    .distinct("scenario_id")
                    .order_by("scenario_id", "-created_at")
                )

                for graph in latest_graphs:
                    scenario_id = str(graph.scenario_id)
                    scenario_graphs[scenario_id] = (
                        graph.graph_config.get("graph_data", {})
                        if graph.graph_config
                        else {}
                    )

                for scenario_id in unique_scenarios:
                    if str(scenario_id) not in scenario_graphs:
                        scenario_graphs[str(scenario_id)] = {}

            # Prepare response
            kpi_data = {
                "total_calls": total_calls,
                "avg_score": avg_score,
                "avg_response": avg_response,
                "calls_attempted": calls_attempted,
                "connected_calls": connected_calls,
                "calls_connected_percentage": calls_connected_percentage,
                "scenario_graphs": scenario_graphs,
                "agent_type": agent_type,
                "is_inbound": is_inbound,
                # Conversation metrics averages (voice)
                "avg_agent_latency": avg_agent_latency,
                "avg_user_interruption_count": avg_user_interruption_count,
                "avg_user_interruption_rate": avg_user_interruption_rate,
                "avg_user_wpm": avg_user_wpm,
                "avg_bot_wpm": avg_bot_wpm,
                "avg_talk_ratio": avg_talk_ratio,
                "avg_ai_interruption_count": avg_ai_interruption_count,
                "avg_ai_interruption_rate": avg_ai_interruption_rate,
                "avg_stop_time_after_interruption": avg_stop_time_after_interruption,
                # Talk percentages (voice)
                "agent_talk_percentage": agent_talk_percentage,
                "customer_talk_percentage": customer_talk_percentage,
                # Chat metrics averages
                "avg_total_tokens": avg_total_tokens,
                "avg_input_tokens": avg_input_tokens,
                "avg_output_tokens": avg_output_tokens,
                "avg_chat_latency_ms": avg_chat_latency_ms,
                "avg_turn_count": avg_turn_count,
                "avg_csat_score": avg_csat_score,
                "failed_calls": failed_calls if failed_calls else 0,
                "total_duration": total_duration,
            }

            # Add evaluation averages to response
            kpi_data.update(eval_averages)

            return Response(kpi_data, status=status.HTTP_200_OK)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to retrieve KPI data: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestCallExecutionsView(APIView):
    """
    API View to retrieve all call executions for a specific run test with pagination and search
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, run_test_id, *args, **kwargs):
        """
        Get all call executions for a specific run test with pagination and search
        Query Parameters:
        - search: search string to filter call executions by phone number or scenario name
        - status: filter by call execution status
        - limit: number of call executions per page (default: 10)
        - page: page number for call executions (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Get query parameters for call executions
            search_query = request.query_params.get("search", "").strip()
            status_filter = request.query_params.get("status", "").strip()

            # Get call executions for this run test (across all test executions)
            call_executions = (
                CallExecution.objects.filter(test_execution__run_test=run_test)
                .select_related(
                    "scenario",
                    "test_execution",
                    "test_execution__simulator_agent",
                    "test_execution__agent_definition",
                )
                .prefetch_related("transcripts", "snapshots")
            )

            # Apply search filter if search query is provided
            if search_query:
                pattern = rf"(?i){re.escape(search_query)}"
                call_executions = call_executions.filter(
                    models.Q(phone_number__regex=pattern)
                    | models.Q(scenario__name__regex=pattern)
                )

            # Apply status filter if provided
            if status_filter:
                call_executions = call_executions.filter(status=status_filter)

            # Get pagination parameters
            page_size = int(request.query_params.get("limit", 10))
            page_number = int(request.query_params.get("page", 1))
            offset = (page_number - 1) * page_size

            # Get SQL query and params for pagination
            union_query, union_params = (
                get_combined_call_executions_and_snapshots_query(
                    run_test_id=run_test.id,
                    search_pattern=search_query,
                    status_filter=status_filter,
                    page_size=page_size,
                    offset=offset,
                )
            )

            # Execute the pagination query
            with connection.cursor() as cursor:
                cursor.execute(union_query, union_params)
                paginated_items = cursor.fetchall()

            # Get total count for pagination metadata
            count_query, count_params = (
                get_combined_call_executions_and_snapshots_count_query(
                    run_test_id=run_test.id,
                    search_pattern=search_query,
                    status_filter=status_filter,
                )
            )

            with connection.cursor() as cursor:
                cursor.execute(count_query, count_params)
                total_items = cursor.fetchone()[0]

            # Process paginated items to get full data
            results = []
            call_exec_ids = []
            snapshot_ids = []

            # Separate IDs by type for batch loading
            for item in paginated_items:
                (
                    item_id,
                    timestamp,
                    item_type,
                    item_status,
                    phone_number,
                    scenario_id,
                    test_execution_id,
                ) = item
                if item_type == "call_execution":
                    call_exec_ids.append(item_id)
                else:
                    snapshot_ids.append(item_id)

            # Batch load call executions
            call_executions_dict = {}
            if call_exec_ids:
                call_execs = (
                    CallExecution.objects.filter(id__in=call_exec_ids)
                    .select_related(
                        "scenario",
                        "test_execution",
                        "test_execution__simulator_agent",
                        "test_execution__agent_definition",
                    )
                    .prefetch_related("transcripts")
                )

                for call_exec in call_execs:
                    call_executions_dict[str(call_exec.id)] = call_exec

            # Batch load snapshots
            snapshots_dict = {}
            if snapshot_ids:
                snapshots = CallExecutionSnapshot.objects.filter(
                    id__in=snapshot_ids
                ).select_related(
                    "call_execution",
                    "call_execution__scenario",
                    "call_execution__test_execution",
                    "call_execution__test_execution__simulator_agent",
                    "call_execution__test_execution__agent_definition",
                )

                for snapshot in snapshots:
                    snapshots_dict[str(snapshot.id)] = snapshot

            # Process items in original order
            for item in paginated_items:
                (
                    item_id,
                    timestamp,
                    item_type,
                    item_status,
                    phone_number,
                    scenario_id,
                    test_execution_id,
                ) = item
                if item_type == "call_execution":
                    call_exec = call_executions_dict.get(str(item_id))
                    if call_exec:
                        serializer = CallExecutionDetailSerializer(call_exec)
                        call_data = serializer.data
                        call_data["is_snapshot"] = False
                        # Remove rerun_snapshots since we're flattening
                        if "rerun_snapshots" in call_data:
                            del call_data["rerun_snapshots"]
                        results.append(call_data)

                else:  # snapshot
                    snapshot = snapshots_dict.get(str(item_id))
                    if snapshot:
                        # Get the original call execution for context
                        original_call_exec = snapshot.call_execution
                        serializer = CallExecutionDetailSerializer(original_call_exec)
                        original_data = serializer.data

                        # Convert snapshot to call execution format
                        snapshot_data = self._convert_snapshot_to_call_execution(
                            CallExecutionSnapshotSerializer(snapshot).data,
                            original_data,
                        )
                        results.append(snapshot_data)

            # Create paginated response
            has_next = offset + page_size < total_items
            has_previous = page_number > 1

            # Calculate total pages

            total_pages = math.ceil(total_items / page_size) if page_size > 0 else 1

            # Build pagination URLs
            base_url = request.build_absolute_uri(request.path)

            # Get all query params
            query_params = request.query_params.dict()

            # Build next URL
            next_url = None
            if has_next:
                next_params = query_params.copy()
                next_params["page"] = page_number + 1
                next_url = f"{base_url}?{urlencode(next_params)}"

            # Build previous URL
            previous_url = None
            if has_previous:
                prev_params = query_params.copy()
                prev_params["page"] = page_number - 1 if page_number > 2 else 1
                # Remove page param if going back to page 1
                if page_number - 1 == 1:
                    prev_params.pop("page", None)
                previous_url = f"{base_url}?{urlencode(prev_params)}"

            response_data = {
                "count": total_items,
                "next": next_url,
                "previous": previous_url,
                "results": results,
                "total_pages": total_pages,
                "current_page": page_number,
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve call executions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _convert_snapshot_to_call_execution(self, snapshot, original_call_exec):
        """Convert a snapshot to call execution format for API response"""
        # Create a call execution object from snapshot data
        snapshot_as_call_exec = {
            # Use snapshot ID as the call execution ID
            "id": snapshot["id"],
            "timestamp": snapshot["snapshot_timestamp"],
            "call_type": snapshot.get("call_type", "Outbound"),
            "status": snapshot.get("status", "completed"),
            "duration": snapshot.get("duration_seconds"),
            "transcript": snapshot.get("transcripts", []),
            "scenario": original_call_exec["scenario"],
            "overall_score": snapshot.get("overall_score"),
            "response_time": snapshot.get("response_time_ms"),
            "audio_url": snapshot.get("recording_url"),
            "customer_name": snapshot.get("customer_number")
            or original_call_exec["customer_name"],
            "eval_outputs": snapshot.get("eval_outputs", {}),
            "eval_metrics": {},  # Will be populated by serializer
            "scenario_columns": original_call_exec["scenario_columns"],
            "error_localizer_tasks": [],  # Snapshots don't have error localizer tasks
            "ended_reason": snapshot.get("ended_reason"),
            "agent_definition_used_name": original_call_exec[
                "agent_definition_used_name"
            ],
            "agent_definition_used_id": original_call_exec["agent_definition_used_id"],
            "call_summary": snapshot.get("call_summary"),
            "recordings": {
                "mono": {
                    "combined_url": snapshot.get("recording_url"),
                    "customer_url": None,
                    "assistant_url": None,
                },
                "stereo_url": snapshot.get("stereo_recording_url"),
            },
            "scenario_id": original_call_exec["scenario_id"],
            "avg_agent_latency": snapshot.get("avg_agent_latency_ms"),
            "user_interruption_count": snapshot.get("user_interruption_count"),
            "user_interruption_rate": snapshot.get("user_interruption_rate"),
            "user_wpm": snapshot.get("user_wpm"),
            "bot_wpm": snapshot.get("bot_wpm"),
            "talk_ratio": snapshot.get("talk_ratio"),
            "ai_interruption_count": snapshot.get("ai_interruption_count"),
            "ai_interruption_rate": snapshot.get("ai_interruption_rate"),
            "avg_stop_time_after_interruption": snapshot.get(
                "avg_stop_time_after_interruption_ms"
            ),
            # Add snapshot metadata
            "is_snapshot": True,
            "snapshot_timestamp": snapshot["snapshot_timestamp"],
            "rerun_type": snapshot["rerun_type"],
            "original_call_execution_id": original_call_exec["id"],
        }

        return snapshot_as_call_exec


class TestExecutionDetailView(APIView):
    """
    API View to retrieve a specific test execution with all its details and paginated call executions
    """

    permission_classes = [IsAuthenticated]
    utils = TestExecutionUtils()

    def get(self, request, test_execution_id, *args, **kwargs):
        """
        Get a specific test execution with all its details and paginated call executions
        Query Parameters:
        - search: search string to filter call executions
        - page: page number for call executions (default: 1)
        - filters: JSON array of filter objects
        - row_groups: JSON array of column IDs to group by
        - group_keys: JSON array of group keys
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the test execution
            test_execution = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
                run_test__deleted=False,
            )

            # Get query parameters
            search_query = request.query_params.get("search", "").strip()
            filters = request.query_params.get("filters", "[]")
            row_groups = request.query_params.get("row_groups", "[]")
            group_keys = request.query_params.get("group_keys", "[]")

            # Parse JSON parameters
            try:
                filters = json.loads(filters) if filters else []
                row_groups = json.loads(row_groups) if row_groups else []
                group_keys = json.loads(group_keys) if group_keys else []
            except json.JSONDecodeError:
                filters = []
                row_groups = []
                group_keys = []

            # Get call executions for this test execution
            call_executions = (
                CallExecution.objects.filter(test_execution=test_execution)
                .select_related(
                    "scenario",
                    "test_execution",
                    "test_execution__simulator_agent",
                    "test_execution__agent_definition",
                )
                .prefetch_related("transcripts", "snapshots", "chat_messages")
            ).order_by("created_at")

            # Get eval configs for filtering
            eval_configs = SimulateEvalConfig.objects.filter(
                run_test=test_execution.run_test, deleted=False
            )
            eval_configs_map = {str(config.id): config for config in eval_configs}

            # Get scenarios for dynamic columns
            scenarios = Scenarios.objects.filter(
                id__in=test_execution.scenario_ids, deleted=False
            )
            scenarios_map = {str(scenario.id): scenario for scenario in scenarios}

            error_messages = []

            # Get agent_type from the test execution's historical data
            run_test = test_execution.run_test
            agent_version = test_execution.agent_version
            agent_definition = test_execution.agent_definition

            if agent_version:
                # Use snapshot for historical agent type
                snapshot = agent_version.configuration_snapshot or {}
                agent_type = (
                    snapshot.get("agent_type")
                    or (
                        agent_version.agent_definition.agent_type
                        if agent_version.agent_definition
                        else None
                    )
                    or (agent_definition.agent_type if agent_definition else None)
                    or AgentDefinition.AgentTypeChoices.VOICE
                )
            elif agent_definition:
                agent_type = agent_definition.agent_type
            elif run_test.agent_definition:
                # Fallback to run_test's agent_definition for legacy executions
                agent_type = run_test.agent_definition.agent_type
            else:
                # Prompt-based simulations are always TEXT/chat type
                agent_type = AgentDefinition.AgentTypeChoices.TEXT

            # Get or create default column order (needed for both grouped and non-grouped responses)
            column_order = test_execution.execution_metadata.get("column_order", [])

            # Normalize legacy camelCase keys and id values in stored column_order
            # entries so the response is always snake_case for the frontend. Old
            # records (and DEFAULT_*_SIM_COL constants) were written with
            # "columnName" keys and camelCase id values like "callDetails";
            # the frontend grid matches cell renderers by snake_case ids, so
            # legacy rows render empty cells until normalized here.
            column_order_mutated = False
            for _col in column_order:
                if not isinstance(_col, dict):
                    continue
                if "columnName" in _col and "column_name" not in _col:
                    _col["column_name"] = _col.pop("columnName")
                    column_order_mutated = True
                legacy_id = _col.get("id")
                if legacy_id in LEGACY_SIM_COLUMN_ID_MAP:
                    _col["id"] = LEGACY_SIM_COLUMN_ID_MAP[legacy_id]
                    column_order_mutated = True
            if column_order_mutated:
                test_execution.execution_metadata["column_order"] = column_order
                test_execution.save(update_fields=["execution_metadata"])

            # Check if column_order has any scenario columns
            has_scenario_columns = any(
                col.get("type") == "scenario_dataset_column" for col in column_order
            )

            if (
                not column_order
                or not test_execution.execution_metadata.get("Provider", False)
                or not has_scenario_columns
            ):
                # Create default column order based on agent type
                if agent_type == AgentDefinition.AgentTypeChoices.VOICE:
                    default_columns = copy.deepcopy(DEFAULT_VOICE_SIM_COL)

                else:
                    # Chat (text) agent type columns with chat metrics from conversation_metrics_data
                    logger.info("Creating default column order for chat agent")
                    default_columns = copy.deepcopy(DEFAULT_CHAT_SIM_COL)

                # Get all scenarios used in this test execution
                scenarios = (
                    Scenarios.objects.filter(
                        id__in=test_execution.scenario_ids, deleted=False
                    )
                    .select_related("dataset")
                    .order_by("name")
                )

                # Collect all column IDs from all scenarios first (batch fetch)
                all_column_ids = set()
                scenario_column_map = {}  # scenario_id -> (dataset_id, column_order)
                for scenario in scenarios:
                    if scenario.dataset and scenario.dataset.column_order:
                        all_column_ids.update(scenario.dataset.column_order)
                        scenario_column_map[scenario.id] = (
                            scenario.dataset.id,
                            scenario.dataset.column_order,
                        )

                # Fetch all columns in a single query
                columns_by_id = {}
                if all_column_ids:
                    columns_by_id = {
                        col.id: col
                        for col in Column.objects.filter(
                            id__in=all_column_ids, deleted=False
                        )
                    }

                # Add scenario columns based on source type
                added_column_ids = set()
                for scenario in scenarios:
                    if scenario.id in scenario_column_map:
                        dataset_id, column_order = scenario_column_map[scenario.id]
                        for col_id in column_order:
                            dataset_column = columns_by_id.get(col_id)
                            if not dataset_column:
                                continue
                            # Skip columns that have already been added to avoid duplicates
                            if dataset_column.id in added_column_ids:
                                continue
                            added_column_ids.add(dataset_column.id)
                            default_columns.append(
                                {
                                    "id": str(dataset_column.id),
                                    "column_name": (
                                        "Ideal Outcome"
                                        if dataset_column.name == "outcome"
                                        else f"{dataset_column.name}"
                                    ),
                                    "visible": True,
                                    "data_type": dataset_column.data_type,
                                    "type": "scenario_dataset_column",
                                    "scenario_id": str(scenario.id),
                                    "dataset_id": str(dataset_id),
                                }
                            )

                # If no columns from scenarios, get from call executions' row datasets
                if not added_column_ids:
                    first_call = call_executions.first()
                    row_id = (
                        first_call.call_metadata.get("row_id")
                        if first_call and first_call.call_metadata
                        else None
                    )
                    if row_id:
                        row = (
                            Row.all_objects.filter(id=row_id)
                            .select_related("dataset")
                            .first()
                        )
                        if row and row.dataset and row.dataset.column_order:
                            row_columns = Column.all_objects.filter(
                                id__in=row.dataset.column_order, deleted=False
                            )
                            for col in row_columns:
                                default_columns.append(
                                    {
                                        "id": str(col.id),
                                        "column_name": (
                                            "Ideal Outcome"
                                            if col.name == "outcome"
                                            else col.name
                                        ),
                                        "visible": True,
                                        "data_type": col.data_type,
                                        "type": "scenario_dataset_column",
                                        "dataset_id": str(row.dataset.id),
                                    }
                                )

                # Add evaluation metrics columns
                for eval_config in eval_configs:
                    default_columns.append(
                        {
                            "column_name": eval_config.name,
                            "id": str(eval_config.id),
                            "eval_config": eval_config.eval_template.config,
                            "visible": True,
                            "type": "evaluation",
                        }
                    )

                # Save the default column order
                test_execution.execution_metadata["column_order"] = default_columns
                test_execution.execution_metadata["Provider"] = True
                test_execution.save(update_fields=["execution_metadata"])
                column_order = default_columns

            # Collect any missing tool evaluation columns from call executions' evaluation_data
            # This ensures that tool columns that were added during execution are included

            if (
                test_execution.status == TestExecution.ExecutionStatus.COMPLETED
                and test_execution.run_test.enable_tool_evaluation
            ):
                existing_column_ids = {col.get("id") for col in column_order}
                existing_tool_column_names = {
                    col.get("column_name") or col.get("columnName")
                    for col in column_order
                    if col.get("type") == "tool_evaluation"
                }

                missing_tool_columns = []
                for call_execution in call_executions:
                    if (
                        call_execution.evaluation_data
                        and "tool_column_order" in call_execution.evaluation_data
                    ):
                        tool_columns = call_execution.evaluation_data.get(
                            "tool_column_order", []
                        )
                        for col in tool_columns:
                            col_id = col.get("id")
                            col_name = col.get("column_name") or col.get("columnName")
                            col_type = col.get("type", "tool_evaluation")

                            # Only add if this column ID doesn't exist and name doesn't exist
                            if (
                                col_id
                                and col_id not in existing_column_ids
                                and col_name not in existing_tool_column_names
                            ):
                                missing_tool_columns.append(col)
                                existing_column_ids.add(col_id)
                                existing_tool_column_names.add(col_name)

                # Append missing tool columns to column_order
                if missing_tool_columns:
                    column_order.extend(missing_tool_columns)
                    # Update test_execution's column_order with the missing columns
                    test_execution.execution_metadata["column_order"] = column_order
                    test_execution.save(update_fields=["execution_metadata"])

            # Ensure voice executions always expose per-call system metric columns.
            if agent_type == AgentDefinition.AgentTypeChoices.VOICE:
                required_voice_columns = {
                    "turn_count": {
                        "column_name": "Turn Count",
                        "id": "turn_count",
                        "visible": True,
                    },
                    "agent_talk_percentage": {
                        "column_name": "Agent Talk (%)",
                        "id": "agent_talk_percentage",
                        "visible": True,
                    },
                }

                existing_by_id = {
                    col.get("id"): col
                    for col in column_order
                    if isinstance(col, dict) and col.get("id")
                }

                did_update_columns = False

                # Add missing columns
                for column_id, required_col in required_voice_columns.items():
                    if column_id not in existing_by_id:
                        column_order.append(copy.deepcopy(required_col))
                        did_update_columns = True

                # Normalize existing column metadata (rename old labels, keep visible).
                # Migrate any legacy camelCase "columnName" key to snake_case "column_name".
                for col in column_order:
                    if not isinstance(col, dict):
                        continue
                    column_id = col.get("id")
                    if column_id not in required_voice_columns:
                        continue

                    # Migrate legacy camelCase key if present
                    if "columnName" in col and "column_name" not in col:
                        col["column_name"] = col.pop("columnName")
                        did_update_columns = True

                    required_col = required_voice_columns[column_id]
                    required_name = required_col["column_name"]
                    if col.get("column_name") != required_name:
                        col["column_name"] = required_name
                        did_update_columns = True

                    if col.get("visible") is False:
                        col["visible"] = True
                        did_update_columns = True

                if did_update_columns:
                    test_execution.execution_metadata["column_order"] = column_order
                    test_execution.save(update_fields=["execution_metadata"])

            # Apply search
            call_executions = self.utils._apply_search(call_executions, search_query)

            # Apply filters
            if filters:
                call_executions = self.utils._apply_filters(
                    call_executions,
                    filters,
                    error_messages,
                    eval_configs_map,
                    column_order=column_order,
                )

            # Apply grouping
            if row_groups:
                call_executions = self.utils._apply_grouping(
                    call_executions,
                    row_groups,
                    group_keys,
                    eval_configs_map,
                    column_order,
                )
                # Grouping now always returns a list, so handle pagination manually
                page = int(request.query_params.get("page", 1))
                page_size = int(request.query_params.get("limit", 30))
                start = (page - 1) * page_size
                end = start + page_size
                paginated_calls = call_executions[start:end]

                # Create a custom paginated response for grouped results
                return Response(
                    {
                        "count": len(call_executions),
                        "next": (
                            f"?page={page + 1}" if end < len(call_executions) else None
                        ),
                        "previous": f"?page={page - 1}" if page > 1 else None,
                        "results": paginated_calls,
                        "total_pages": (len(call_executions) + page_size - 1)
                        // page_size,
                        "current_page": page,
                        "column_order": column_order,
                        "error_messages": error_messages,
                    },
                    status=status.HTTP_200_OK,
                )

            # Order by updated date (newest/most recently rerun first) - only for non-grouped results
            call_executions = call_executions.order_by("-updated_at")

            # Apply pagination to call executions
            paginator = ExtendedPageNumberPagination()
            try:
                paginated_calls = paginator.paginate_queryset(call_executions, request)
            except NotFound:
                # Invalid page (e.g. page 2 when only 1 page exists) — return early
                total_count = call_executions.count()
                try:
                    page_size = int(request.query_params.get("limit", 30))
                except (ValueError, TypeError):
                    page_size = 30
                total_pages = (
                    math.ceil(total_count / page_size)
                    if total_count > 0 and page_size > 0
                    else 0
                )
                return Response(
                    {
                        "count": total_count,
                        "next": None,
                        "previous": None,
                        "results": [],
                        "total_pages": total_pages,
                        "current_page": int(request.query_params.get("page", 1) or 1),
                        "column_order": column_order,
                        "error_messages": error_messages,
                        "status": test_execution.status,
                    },
                    status=status.HTTP_200_OK,
                )

            # Bulk fetch dataset session_ids (Row.metadata["session_id"]) for this page to avoid N+1.
            row_ids = set()
            for call_execution in paginated_calls:
                row_id = getattr(call_execution, "row_id", None)
                if (
                    not row_id
                    and hasattr(call_execution, "call_metadata")
                    and isinstance(call_execution.call_metadata, dict)
                ):
                    row_id = call_execution.call_metadata.get("row_id")
                if row_id:
                    row_ids.add(str(row_id))

            # Check if any scenario is from a replay session — only query if
            # there are scenario IDs to avoid a pointless DB hit
            is_replay = (
                bool(test_execution.scenario_ids)
                and Scenarios.objects.filter(
                    id__in=test_execution.scenario_ids,
                    deleted=False,
                    metadata__created_from="replay_session",
                ).exists()
            )

            row_session_id_map = {}
            if row_ids:
                for row_id, metadata in Row.all_objects.filter(
                    id__in=row_ids
                ).values_list("id", "metadata"):
                    if not isinstance(metadata, dict):
                        continue
                    # Chat replays use session_id, voice replays use trace_id.
                    # For replay sessions, intent_id is the baseline trace ID.
                    baseline_id = metadata.get("session_id") or metadata.get("trace_id")
                    if not baseline_id and is_replay:
                        baseline_id = metadata.get("intent_id")
                    if baseline_id:
                        row_session_id_map[str(row_id)] = baseline_id

            # Batch-prefetch scenario column data (Row, Column, Cell) to avoid N+1 in serializer
            rows_map = {}  # row_id -> Row instance
            columns_by_dataset = {}  # dataset_id -> [Column, ...]
            cells_by_row = {}  # row_id -> {column_id -> Cell}
            if row_ids:
                rows_qs = Row.all_objects.filter(id__in=row_ids).select_related(
                    "dataset"
                )
                for row in rows_qs:
                    rows_map[str(row.id)] = row

                # Collect all dataset column_order IDs across all rows
                all_column_ids = set()
                dataset_ids_seen = set()
                for row in rows_map.values():
                    if row.dataset and row.dataset.id not in dataset_ids_seen:
                        dataset_ids_seen.add(row.dataset.id)
                        all_column_ids.update(row.dataset.column_order or [])

                # Batch-fetch all columns
                columns_qs = Column.all_objects.filter(id__in=list(all_column_ids))
                columns_map = {str(col.id): col for col in columns_qs}
                for row in rows_map.values():
                    if row.dataset:
                        ds_id = str(row.dataset.id)
                        if ds_id not in columns_by_dataset:
                            columns_by_dataset[ds_id] = [
                                columns_map[str(cid)]
                                for cid in (row.dataset.column_order or [])
                                if str(cid) in columns_map
                            ]

                # Batch-fetch all cells for these rows
                cells_qs = Cell.all_objects.filter(row_id__in=list(row_ids))
                for cell in cells_qs:
                    r_id = str(cell.row_id)
                    c_id = str(cell.column_id)
                    if r_id not in cells_by_row:
                        cells_by_row[r_id] = {}
                    cells_by_row[r_id][c_id] = cell

            # Batch-prefetch rerun snapshots for paginated calls
            call_ids = [str(ce.id) for ce in paginated_calls]
            snapshots_by_call = {}
            if call_ids:
                snapshots_qs = CallExecutionSnapshot.objects.filter(
                    call_execution_id__in=call_ids,
                    rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
                ).order_by("-snapshot_timestamp")
                for snapshot in snapshots_qs:
                    ce_id = str(snapshot.call_execution_id)
                    if ce_id not in snapshots_by_call:
                        snapshots_by_call[ce_id] = []
                    snapshots_by_call[ce_id].append(snapshot)

            # Serialize the paginated call executions with new structure
            # Pass eval_configs and scenarios as context for the serializer
            call_executions_serializer = CallExecutionDetailSerializer(
                paginated_calls,
                many=True,
                context={
                    "eval_configs": eval_configs_map,
                    "scenarios": scenarios_map,
                    "row_session_id_map": row_session_id_map,
                    "rows_map": rows_map,
                    "columns_by_dataset": columns_by_dataset,
                    "cells_by_row": cells_by_row,
                    "snapshots_by_call": snapshots_by_call,
                    "detail_mode": False,
                },
            )

            # Get paginated response with proper structure
            paginated_response = paginator.get_paginated_response(
                call_executions_serializer.data
            )

            # Add column order and metadata to response
            response_data = paginated_response.data
            response_data["column_order"] = column_order
            response_data["error_messages"] = error_messages
            response_data["status"] = test_execution.status
            response_data["provider"] = (
                test_execution.agent_definition.provider
                if test_execution.agent_definition
                else "prompt"
            )
            response_data["agent_type"] = agent_type

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to retrieve test execution: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PerformanceSummaryView(APIView):
    """
    API View to retrieve performance summary data for a specific test execution
    Focuses on Test Run Performance Metrics and Top Performing Scenarios

    Endpoint: GET /simulate/test-executions/{test_execution_id}/performance-summary/

    Response Format:
    {
        "test_run_performance_metrics": {
            "pass_rate": 80.0,
            "total_test_runs": 45,
            "latest_fail_rate": 8.0
        },
        "top_performing_scenarios": [
            {
                "scenario_name": "Password Reset",
                "test_count": 15,
                "performance_score": 8.9
            },
            {
                "scenario_name": "Product Inquiry",
                "test_count": 12,
                "performance_score": 8.2
            }
        ]
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, test_execution_id, *args, **kwargs):
        """
        Get performance summary data for a specific test execution
        Returns:
        - Test Run Performance Metrics (Pass Rate, Total Test Runs, Latest Fail Rate)
        - Top Performing Scenarios with their performance scores
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the test execution
            test_execution = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
                run_test__deleted=False,
            )

            # Get all call executions for this test execution
            call_executions = CallExecution.objects.filter(
                test_execution=test_execution
            ).select_related("scenario", "test_execution")

            # Calculate Test Run Performance Metrics in a single query
            from django.db.models import Count, Q

            stats = call_executions.aggregate(
                total=Count("id"),
                completed=Count("id", filter=Q(status="completed")),
                failed=Count("id", filter=Q(status="failed")),
            )
            total_test_runs = stats["total"]
            completed_calls = stats["completed"]
            failed_calls = stats["failed"]

            # Calculate pass rate (completed calls / total calls)
            pass_rate = (
                round((completed_calls / total_test_runs * 100), 1)
                if total_test_runs > 0
                else 0
            )

            # Calculate latest fail rate (failed calls / total calls)
            latest_fail_rate = (
                round((failed_calls / total_test_runs * 100), 1)
                if total_test_runs > 0
                else 0
            )

            # Calculate scenario performance scores
            scenario_performance = {}

            for call_execution in call_executions:
                scenario_id = str(call_execution.scenario.id)
                scenario_name = call_execution.scenario.name

                if scenario_id not in scenario_performance:
                    scenario_performance[scenario_id] = {
                        "name": scenario_name,
                        "test_count": 0,
                        "total_score": 0,
                        "scores": [],
                    }

                scenario_performance[scenario_id]["test_count"] += 1

                # Get overall score if available
                if call_execution.overall_score is not None:
                    scenario_performance[scenario_id][
                        "total_score"
                    ] += call_execution.overall_score
                    scenario_performance[scenario_id]["scores"].append(
                        call_execution.overall_score
                    )

            # Calculate average scores for each scenario
            top_performing_scenarios = []
            for _scenario_id, data in scenario_performance.items():
                if data["scores"]:
                    avg_score = data["total_score"] / len(data["scores"])
                    top_performing_scenarios.append(
                        {
                            "scenario_name": data["name"],
                            "test_count": data["test_count"],
                            "performance_score": round(avg_score, 1),
                        }
                    )
                else:
                    # If no scores available, use a default score of 0
                    top_performing_scenarios.append(
                        {
                            "scenario_name": data["name"],
                            "test_count": data["test_count"],
                            "performance_score": 0.0,
                        }
                    )

            # Sort scenarios by performance score (highest first)
            top_performing_scenarios.sort(
                key=lambda x: x["performance_score"], reverse=True
            )

            # Limit to top 4 scenarios (as shown in the image)
            top_performing_scenarios = top_performing_scenarios[:4]

            # Prepare response data
            response_data = {
                "test_run_performance_metrics": {
                    "pass_rate": pass_rate,
                    "total_test_runs": total_test_runs,
                    "latest_fail_rate": latest_fail_rate,
                },
                "top_performing_scenarios": top_performing_scenarios,
            }

            # Validate and serialize the response data
            serializer = PerformanceSummarySerializer(data=response_data)
            serializer.is_valid(raise_exception=True)

            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to retrieve performance summary: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestExecutionAnalyticsView(APIView):
    """
    API View to retrieve analytics data for a specific test execution
    Provides data for charts including fail rate over test runs and evaluation categories over test runs

    Endpoint: GET /simulate/test-executions/{test_execution_id}/analytics/

    Response Format:
    {
        "fail_rate_over_test_runs": {
            "title": "Fail Rate Over Test Runs",
            "data": [
                {"test_run": 1, "fail_rate": 25.0, "total_calls": 10, "failed_calls": 2},
                {"test_run": 2, "fail_rate": 15.0, "total_calls": 10, "failed_calls": 1}
            ],
            "x_axis_label": "Test Runs",
            "y_axis_label": "Fail Rate (%)",
            "chart_type": "scatter"
        },
        "evaluation_categories_over_test_runs": {
            "title": "Evaluation Categories Over Test Runs",
            "data": [
                {"test_run": 1, "score_percentage": 85.0, "total_calls": 10, "scored_calls": 8},
                {"test_run": 2, "score_percentage": 90.0, "total_calls": 10, "scored_calls": 9}
            ],
            "x_axis_label": "Test Runs",
            "y_axis_label": "Score (%)",
            "chart_type": "line"
        },
        "metadata": {
            "total_test_runs": 15,
            "total_calls": 150,
            "test_execution_id": "uuid",
            "test_execution_name": "Test Name"
        }
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, test_execution_id, *args, **kwargs):
        """
        Get analytics data for a specific test execution
        Returns:
        - Fail Rate Over Test Runs (scatter plot data)
        - Evaluation Categories Over Test Runs (line graph data)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the test execution
            test_execution = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
                run_test__deleted=False,
            )

            # Get all call executions for this test execution, ordered by creation time
            call_executions = (
                CallExecution.objects.filter(test_execution=test_execution)
                .select_related("scenario", "test_execution")
                .order_by("created_at")
            )

            # Calculate Fail Rate Over Test Runs
            fail_rate_data = []
            total_calls = call_executions.count()

            if total_calls > 0:
                # Group calls by batches to simulate test runs
                # For simplicity, we'll create batches of calls and calculate fail rate for each batch
                batch_size = max(1, total_calls // 15)  # Create up to 15 data points
                current_batch = []
                test_run_number = 1

                for call_execution in call_executions:
                    current_batch.append(call_execution)

                    if len(current_batch) >= batch_size and test_run_number <= 15:
                        # Calculate fail rate for this batch
                        batch_total = len(current_batch)
                        batch_failed = sum(
                            1 for call in current_batch if call.status == "failed"
                        )
                        fail_rate = (
                            round((batch_failed / batch_total * 100), 1)
                            if batch_total > 0
                            else 0
                        )

                        fail_rate_data.append(
                            {
                                "test_run": test_run_number,
                                "fail_rate": fail_rate,
                                "total_calls": batch_total,
                                "failed_calls": batch_failed,
                            }
                        )

                        current_batch = []
                        test_run_number += 1

                # Handle remaining calls in the last batch
                if current_batch and test_run_number <= 15:
                    batch_total = len(current_batch)
                    batch_failed = sum(
                        1 for call in current_batch if call.status == "failed"
                    )
                    fail_rate = (
                        round((batch_failed / batch_total * 100), 1)
                        if batch_total > 0
                        else 0
                    )

                    fail_rate_data.append(
                        {
                            "test_run": test_run_number,
                            "fail_rate": fail_rate,
                            "total_calls": batch_total,
                            "failed_calls": batch_failed,
                        }
                    )

            # Calculate Evaluation Categories Over Test Runs
            evaluation_data = []

            if total_calls > 0:
                # Use the same batching logic for evaluation scores
                current_batch = []
                test_run_number = 1

                for call_execution in call_executions:
                    current_batch.append(call_execution)

                    if len(current_batch) >= batch_size and test_run_number <= 15:
                        # Calculate average score for this batch
                        valid_scores = [
                            call.overall_score
                            for call in current_batch
                            if call.overall_score is not None
                        ]

                        if valid_scores:
                            avg_score = sum(valid_scores) / len(valid_scores)
                            # Convert to percentage (assuming scores are 0-1, convert to 0-100)
                            score_percentage = round(avg_score * 100, 1)
                        else:
                            score_percentage = 0.0

                        evaluation_data.append(
                            {
                                "test_run": test_run_number,
                                "score_percentage": score_percentage,
                                "total_calls": len(current_batch),
                                "scored_calls": len(valid_scores),
                            }
                        )

                        current_batch = []
                        test_run_number += 1

                # Handle remaining calls in the last batch
                if current_batch and test_run_number <= 15:
                    valid_scores = [
                        call.overall_score
                        for call in current_batch
                        if call.overall_score is not None
                    ]

                    if valid_scores:
                        avg_score = sum(valid_scores) / len(valid_scores)
                        score_percentage = round(avg_score * 100, 1)
                    else:
                        score_percentage = 0.0

                    evaluation_data.append(
                        {
                            "test_run": test_run_number,
                            "score_percentage": score_percentage,
                            "total_calls": len(current_batch),
                            "scored_calls": len(valid_scores),
                        }
                    )

            # Prepare response data
            response_data = {
                "fail_rate_over_test_runs": {
                    "title": "Fail Rate Over Test Runs",
                    "data": fail_rate_data,
                    "x_axis_label": "Test Runs",
                    "y_axis_label": "Fail Rate (%)",
                    "chart_type": "scatter",
                },
                "evaluation_categories_over_test_runs": {
                    "title": "Evaluation Categories Over Test Runs",
                    "data": evaluation_data,
                    "x_axis_label": "Test Runs",
                    "y_axis_label": "Score (%)",
                    "chart_type": "line",
                },
                "metadata": {
                    "total_test_runs": len(fail_rate_data),
                    "total_calls": total_calls,
                    "test_execution_id": str(test_execution.id),
                    "test_execution_name": test_execution.run_test.name,
                },
            }

            # Validate and serialize the response data
            serializer = TestExecutionAnalyticsSerializer(data=response_data)
            serializer.is_valid(raise_exception=True)

            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to retrieve analytics data: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestAnalyticsView(APIView):
    """
    API View to retrieve analytics data for a specific run test across multiple test executions
    Provides aggregated analytics data for comparison across different test runs

    Endpoint: GET /simulate/run-tests/{run_test_id}/analytics/

    Response Format:
    {
        "run_test_info": {
            "id": "uuid",
            "name": "Test Name",
            "description": "Test Description",
            "total_test_executions": 5,
            "total_calls": 150
        },
        "fail_rate_trends": [
            {
                "test_execution_id": "uuid",
                "test_execution_name": "Execution 2024-01-15 10:30",
                "created_at": "2024-01-15T10:30:00Z",
                "fail_rate": 15.0,
                "total_calls": 30,
                "failed_calls": 4
            }
        ],
        "evaluation_score_trends": [
            {
                "test_execution_id": "uuid",
                "test_execution_name": "Execution 2024-01-15 10:30",
                "created_at": "2024-01-15T10:30:00Z",
                "score_percentage": 85.0,
                "total_calls": 30,
                "scored_calls": 25
            }
        ],
        "performance_comparison": [
            {
                "test_execution_id": "uuid",
                "test_execution_name": "Execution 2024-01-15 10:30",
                "created_at": "2024-01-15T10:30:00Z",
                "status": "completed",
                "total_calls": 30,
                "completed_calls": 26,
                "failed_calls": 4,
                "success_rate": 86.7,
                "fail_rate": 13.3,
                "avg_score": 85.0,
                "duration_seconds": 180
            }
        ],
        "summary_stats": {
            "avg_success_rate": 85.0,
            "avg_fail_rate": 15.0,
            "avg_evaluation_score": 87.5,
            "total_executions": 5,
            "best_success_rate": 95.0,
            "worst_success_rate": 75.0,
            "best_evaluation_score": 92.0,
            "worst_evaluation_score": 80.0
        }
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, run_test_id, *args, **kwargs):
        """
        Get analytics data for a specific run test across multiple test executions
        Returns:
        - Aggregated fail rate trends
        - Aggregated evaluation score trends
        - Performance comparison across test executions
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Get all test executions for this run test, ordered by creation time
            test_executions = TestExecution.objects.filter(
                run_test=run_test, deleted=False
            ).order_by("created_at")

            # Get all call executions for all test executions
            call_executions = (
                CallExecution.objects.filter(test_execution__in=test_executions)
                .select_related("test_execution", "scenario")
                .order_by("created_at")
            )

            # Calculate aggregated analytics data
            analytics_data = {
                "run_test_info": {
                    "id": str(run_test.id),
                    "name": run_test.name,
                    "description": run_test.description,
                    "total_test_executions": test_executions.count(),
                    "total_calls": call_executions.count(),
                },
                "fail_rate_trends": [],
                "evaluation_score_trends": [],
                "performance_comparison": [],
            }

            # Calculate trends across test executions
            for test_execution in test_executions:
                execution_calls = call_executions.filter(test_execution=test_execution)
                total_calls = execution_calls.count()

                if total_calls > 0:
                    # Calculate fail rate for this test execution
                    failed_calls = execution_calls.filter(status="failed").count()
                    fail_rate = round((failed_calls / total_calls * 100), 1)

                    # Calculate average evaluation score for this test execution
                    valid_scores = [
                        call.overall_score
                        for call in execution_calls
                        if call.overall_score is not None
                    ]
                    avg_score = 0.0
                    if valid_scores:
                        avg_score = sum(valid_scores) / len(valid_scores)
                        avg_score_percentage = round(avg_score * 100, 1)
                    else:
                        avg_score_percentage = 0.0

                    # Add to trends
                    analytics_data["fail_rate_trends"].append(
                        {
                            "test_execution_id": str(test_execution.id),
                            "test_execution_name": f"Execution {test_execution.created_at.strftime('%Y-%m-%d %H:%M')}",
                            "created_at": test_execution.created_at.isoformat(),
                            "fail_rate": fail_rate,
                            "total_calls": total_calls,
                            "failed_calls": failed_calls,
                        }
                    )

                    analytics_data["evaluation_score_trends"].append(
                        {
                            "test_execution_id": str(test_execution.id),
                            "test_execution_name": f"Execution {test_execution.created_at.strftime('%Y-%m-%d %H:%M')}",
                            "created_at": test_execution.created_at.isoformat(),
                            "score_percentage": avg_score_percentage,
                            "total_calls": total_calls,
                            "scored_calls": len(valid_scores),
                        }
                    )

                    # Performance comparison data
                    analytics_data["performance_comparison"].append(
                        {
                            "test_execution_id": str(test_execution.id),
                            "test_execution_name": f"Execution {test_execution.created_at.strftime('%Y-%m-%d %H:%M')}",
                            "created_at": test_execution.created_at.isoformat(),
                            "status": test_execution.status,
                            "total_calls": total_calls,
                            "completed_calls": test_execution.completed_calls,
                            "failed_calls": test_execution.failed_calls,
                            "success_rate": test_execution.success_rate,
                            "fail_rate": fail_rate,
                            "avg_score": avg_score_percentage,
                            "duration_seconds": test_execution.duration_seconds,
                        }
                    )

            # Sort trends by creation date
            analytics_data["fail_rate_trends"].sort(key=lambda x: x["created_at"])
            analytics_data["evaluation_score_trends"].sort(
                key=lambda x: x["created_at"]
            )
            analytics_data["performance_comparison"].sort(key=lambda x: x["created_at"])

            # Add summary statistics
            if analytics_data["performance_comparison"]:
                success_rates = [
                    p["success_rate"] for p in analytics_data["performance_comparison"]
                ]
                fail_rates = [
                    p["fail_rate"] for p in analytics_data["performance_comparison"]
                ]
                avg_scores = [
                    p["avg_score"] for p in analytics_data["performance_comparison"]
                ]

                analytics_data["summary_stats"] = {
                    "avg_success_rate": round(
                        sum(success_rates) / len(success_rates), 1
                    ),
                    "avg_fail_rate": round(sum(fail_rates) / len(fail_rates), 1),
                    "avg_evaluation_score": round(sum(avg_scores) / len(avg_scores), 1),
                    "total_executions": len(analytics_data["performance_comparison"]),
                    "best_success_rate": max(success_rates),
                    "worst_success_rate": min(success_rates),
                    "best_evaluation_score": max(avg_scores),
                    "worst_evaluation_score": min(avg_scores),
                }

            return Response(analytics_data, status=status.HTTP_200_OK)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to retrieve run test analytics: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CallExecutionDetailView(APIView):
    """
    API View to retrieve a specific call execution with all its details
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, call_execution_id, *args, **kwargs):
        """Get a specific call execution with all its details"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__run_test__organization=user_organization,
                test_execution__run_test__deleted=False,
            )

            # Mirror the list endpoint's lookup so the drawer's
            # "Compare with baseline" button stays visible after the
            # detail fetch (session_id → trace_id → intent_id for replay).
            row_session_id_map = {}
            row_id = getattr(call_execution, "row_id", None)
            if not row_id and isinstance(call_execution.call_metadata, dict):
                row_id = call_execution.call_metadata.get("row_id")
            if row_id:
                row = (
                    Row.all_objects.filter(id=row_id)
                    .only("id", "metadata")
                    .first()
                )
                if row and isinstance(row.metadata, dict):
                    baseline_id = row.metadata.get(
                        "session_id"
                    ) or row.metadata.get("trace_id")
                    if not baseline_id:
                        test_execution = call_execution.test_execution
                        scenario_ids = getattr(
                            test_execution, "scenario_ids", None
                        )
                        if scenario_ids and Scenarios.objects.filter(
                            id__in=scenario_ids,
                            deleted=False,
                            metadata__created_from="replay_session",
                        ).exists():
                            baseline_id = row.metadata.get("intent_id")
                    if baseline_id:
                        row_session_id_map[str(row_id)] = baseline_id

            # Serialize with the same serializer as the list view, but with full detail
            serializer = CallExecutionDetailSerializer(
                call_execution,
                context={
                    "request": request,
                    "eval_configs": {},
                    "scenarios": {},
                    "row_session_id_map": row_session_id_map,
                    "rows_map": {},
                    "columns_by_dataset": {},
                    "cells_by_row": {},
                    "snapshots_by_call": {},
                    "detail_mode": True,
                },
            )

            # Attach trace_details (trace_id, parent_span_id, attributes,
            # simulated_sessions) so the voice drawer's Attributes tab has
            # data to render. The serializer itself can't populate this
            # without an extra ObservationSpan query per instance, so we
            # add it here in the detail view where a one-row lookup is cheap.
            response_data = dict(serializer.data)
            # add_trace_details_to_call_executions([response_data])

            # Shape parity with the observe drawer: when a trace is linked,
            # also return the full serialized observation spans array. The
            # voice drawer's unified data path (`data.observation_span`)
            # then works the same in simulate and observe modes — same
            # Attributes tab code path, same Logs code path. If no linkage
            # exists (e.g. older tests whose spans were never ingested),
            # this stays absent and the drawer falls back to
            # `trace_details.attributes` as before.
            trace_id = (response_data.get("trace_details") or {}).get("trace_id")
            if trace_id:
                from tracer.serializers.observation_span import (
                    ObservationSpanSerializer,
                )

                trace = (
                    Trace.objects.filter(id=trace_id)
                    .prefetch_related("observation_spans")
                    .first()
                )
                if trace:
                    response_data["observation_span"] = [
                        ObservationSpanSerializer(span).data
                        for span in trace.observation_spans.all()
                    ]

            # Observability-off calls have no ObservationSpan but we still
            # hold the full provider payload on `provider_call_data`. We
            # reuse the same flattener the ingest pipeline runs for
            # observability-on calls (`_extract_eval_attributes`) so the
            # drawer's Attributes tab sees the exact same shape — flat
            # keys like `raw_log`, `vapi.call_id`, `call.duration`,
            # `cost_breakdown.*`, `gen_ai.*` — instead of a single
            # collapsed raw_log tree. `include_call_logs=False` skips the
            # blocking GET against `artifact.logUrl`; CallExecutionLogsView
            # handles that asynchronously via the ingest task.
            pcd = call_execution.provider_call_data or {}
            vapi_data = pcd.get("vapi") if isinstance(pcd.get("vapi"), dict) else None
            if vapi_data:
                from tracer.utils.vapi import _extract_eval_attributes

                response_data["attributes"] = _extract_eval_attributes(
                    vapi_data, include_call_logs=False
                )
            else:
                # Fallback for other providers (retell etc.): ship the raw
                # payload under `raw_log` so the Attributes tab at least
                # renders the full object tree.
                provider_data = next(
                    (v for v in pcd.values() if isinstance(v, dict)), None
                )
                if provider_data:
                    response_data["attributes"] = {"raw_log": provider_data}

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(
                "error_retrieving_call_execution",
                call_execution_id=str(call_execution_id),
                error=str(e),
            )
            return Response(
                {"error": f"Failed to retrieve call execution: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # used only for marking call failed.
    def patch(self, request, call_execution_id, *args, **kwargs):
        """Update the status of a specific call execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return self.gm.bad_request("Organization not found for the user")

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__run_test__organization=user_organization,
                test_execution__run_test__deleted=False,
            )

            # Validate request data
            new_status = request.data.get("status")

            if isinstance(new_status, str):
                new_status = new_status.lower()
            # Do NOT persist raw error details from the client into DB.
            # Use a safe generic reason instead (prevents leaking internal errors/stacktraces).
            generic_failure_reason = "Error processing simulation"
            ended_reason = request.data.get("ended_reason")

            if not new_status:
                return self.gm.bad_request("Status is required")

            # Validate status is a valid choice
            valid_statuses = [choice[0] for choice in CallExecution.CallStatus.choices]
            if new_status not in valid_statuses:
                return self.gm.bad_request(
                    f"Invalid status. Valid choices are: {', '.join(valid_statuses)}"
                )

            # Update status atomically
            with transaction.atomic():
                # Lock the call execution to prevent race conditions
                call_execution_locked = CallExecution.objects.select_for_update().get(
                    id=call_execution_id
                )

                old_status = call_execution_locked.status
                call_execution_locked.status = new_status

                # Update timestamps based on status
                now = timezone.now()
                if new_status in [
                    CallExecution.CallStatus.FAILED,
                    CallExecution.CallStatus.CANCELLED,
                ]:
                    if not call_execution_locked.ended_at:
                        call_execution_locked.ended_at = now

                # Only store a generic reason (never raw error text from request).
                if new_status == CallExecution.CallStatus.FAILED:
                    call_execution_locked.ended_reason = generic_failure_reason
                    ended_reason = generic_failure_reason
                elif (
                    new_status == CallExecution.CallStatus.CANCELLED
                    and ended_reason is not None
                ):
                    # For cancellations, keep existing behavior (optional reason), but avoid accidental leakage.
                    call_execution_locked.ended_reason = str(ended_reason)[:200]
                    ended_reason = call_execution_locked.ended_reason
                elif (
                    new_status == CallExecution.CallStatus.COMPLETED
                    and ended_reason is not None
                ):
                    # For completed with error (partial completion), store the error reason
                    # This happens when a chat had some successful turns before failing
                    call_execution_locked.ended_reason = str(ended_reason)[:200]
                    ended_reason = call_execution_locked.ended_reason

                # Save the updated call execution
                update_fields = ["status", "updated_at"]
                if new_status in [
                    CallExecution.CallStatus.FAILED,
                    CallExecution.CallStatus.CANCELLED,
                    CallExecution.CallStatus.COMPLETED,
                ]:
                    if not call_execution_locked.ended_at:
                        call_execution_locked.ended_at = now
                    update_fields.append("ended_at")
                if ended_reason is not None:
                    update_fields.append("ended_reason")

                call_execution_locked.save(update_fields=update_fields)

                logger.info(
                    "call_execution_status_updated",
                    call_execution_id=str(call_execution_locked.id),
                    old_status=old_status,
                    new_status=new_status,
                    ended_reason=ended_reason,
                )

            # Serialize and return the updated call execution
            serializer = CallExecutionSerializer(
                call_execution_locked, context={"request": request}
            )

            return Response(serializer.data, status=status.HTTP_200_OK)

        except CallExecution.DoesNotExist:
            return self.gm.bad_request("Call execution not found")
        except Exception as e:
            logger.exception(
                "error_updating_call_execution_status",
                call_execution_id=str(call_execution_id),
                error=str(e),
            )
            return Response(
                {"error": "Failed to update call execution status"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CallExecutionLogsView(APIView):
    """
    Paginated API to retrieve stored log entries for a call execution.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()
        self.pagination_class = ExtendedPageNumberPagination()

    def get(self, request, call_execution_id, *args, **kwargs):
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            if not user_organization:
                return self.gm.bad_request("Organization not found for the user.")
            customer_call_id = request.query_params.get(
                "customer_call_id"
            ) or request.query_params.get("vapi_call_id")
            if customer_call_id:
                call_execution = CallExecution.objects.filter(
                    customer_call_id=customer_call_id
                ).first()
                if not call_execution:
                    return self.gm.bad_request("Call execution not found.")
            else:
                call_execution = get_object_or_404(
                    CallExecution,
                    id=call_execution_id,
                    test_execution__run_test__organization=user_organization,
                    test_execution__run_test__deleted=False,
                )

            source = CallLogEntry.LogSource.CUSTOMER

            queryset = CallLogEntry.objects.filter(
                call_execution=call_execution, source=source
            )

            severity_text = request.query_params.get("severity_text")
            if severity_text is not None:
                try:
                    queryset = queryset.filter(severity_text=severity_text)
                except (TypeError, ValueError):
                    pass

            category = request.query_params.get("category")
            if category:
                queryset = queryset.filter(category__iexact=category)

            search_term = request.query_params.get("search")
            if search_term:
                queryset = queryset.filter(payload__icontains=search_term)

            queryset = queryset.order_by("logged_at", "created_at")

            # Lazy backfill: observability-off simulate calls never had their
            # artifact log file downloaded at ingest time, but the URL is
            # sitting on `provider_call_data.vapi.artifact.logUrl`. First
            # Logs-tab open triggers the existing ingest task; subsequent
            # opens serve the persisted CallLogEntry rows. `customer_log_url`
            # doubles as the dispatched-flag so we don't re-fire on every
            # poll while the task runs.
            if not queryset.exists() and not call_execution.customer_log_url:
                pcd = call_execution.provider_call_data or {}
                vapi = pcd.get("vapi") or {}
                log_url = (vapi.get("artifact") or {}).get("logUrl")
                if log_url:
                    call_execution.customer_log_url = log_url
                    call_execution.save(update_fields=["customer_log_url"])
                    from simulate.tasks.call_log_tasks import (
                        ingest_call_logs_task,
                    )

                    ingest_call_logs_task.apply_async(
                        args=(str(call_execution.id), log_url),
                        kwargs={
                            "verify_ssl": False,
                            "source": CallLogEntry.LogSource.CUSTOMER,
                        },
                    )

            # Intentionally return a 200 with an empty page when no rows
            # match — the frontend Logs tab distinguishes "no logs yet /
            # ingestion pending" from real errors by the HTTP status.
            paginator = self.pagination_class
            paginated_entries = paginator.paginate_queryset(
                queryset, request, view=self
            )

            results = [
                {
                    "id": str(entry.id),
                    "logged_at": entry.logged_at.isoformat(),
                    "level": entry.level,
                    "severity_text": entry.severity_text,
                    "category": entry.category,
                    "body": entry.body,
                    "attributes": entry.attributes,
                    "payload": entry.payload,
                }
                for entry in paginated_entries
            ]

            return paginator.get_paginated_response(
                {
                    "results": results,
                    "source": source,
                }
            )

        except Exception as e:  # noqa: BLE001
            logger.exception("Failed to fetch call execution logs")
            return self.gm.internal_server_error_response(
                f"Failed to retrieve call execution logs: {str(e)}"
            )


class TestExecutionColumnOrderView(APIView):
    """
    API View to update column order for a test execution
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def put(self, request, test_execution_id, *args, **kwargs):
        """Update column order for a test execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the test execution
            test_execution = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
                run_test__deleted=False,
            )

            # Validate request data
            serializer = TestExecutionColumnOrderSerializer(data=request.data)
            if not serializer.is_valid():
                return self.gm.bad_request("Invalid column order data")

            # Update column order in execution_metadata
            column_order = serializer.validated_data["column_order"]
            test_execution.execution_metadata["column_order"] = column_order
            test_execution.save()

            return Response(
                {
                    "message": "Column order updated successfully",
                    "column_order": column_order,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to update column order: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestExecutionDeleteView(APIView):
    """
    API View to delete a specific test execution
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def delete(self, request, test_execution_id, *args, **kwargs):
        """Delete a specific test execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the test execution
            test_execution = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
                run_test__deleted=False,
            )

            # Check if test execution is currently running
            if test_execution.status == TestExecution.ExecutionStatus.RUNNING:
                return self.gm.bad_request(
                    "Cannot delete a test execution that is currently running."
                )

            # Delete the test execution (this will cascade to call executions)
            test_execution.delete()

            return Response(
                {"message": "Test execution deleted successfully."},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to delete test execution: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestExecutionBulkDeleteView(APIView):
    """
    API View to handle bulk test execution delete requests.
    Deletes multiple test executions within a run test.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gm = GeneralMethods()

    def post(self, request, run_test_id):
        """
        Delete multiple test executions within a run test.

        Args:
            run_test_id: UUID of the RunTest containing the test executions to delete
        """
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                organization=user_organization,
                deleted=False,
            )

            serializer = TestExecutionBulkDeleteSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request("Invalid request data")

            select_all = serializer.validated_data.get("select_all", False)
            test_execution_ids = serializer.validated_data.get("test_execution_ids", [])

            # Get test executions to delete
            if select_all:
                test_executions = TestExecution.objects.filter(run_test=run_test)
                if test_execution_ids:
                    test_executions = test_executions.exclude(id__in=test_execution_ids)
            else:
                test_executions = TestExecution.objects.filter(
                    id__in=test_execution_ids, run_test=run_test
                )

            if not test_executions.exists():
                return self._gm.bad_request(
                    "No test executions found that can be deleted."
                )

            # Check for active test executions (running, pending, or cancelling)
            non_deletable_statuses = [
                TestExecution.ExecutionStatus.RUNNING,
                TestExecution.ExecutionStatus.PENDING,
                TestExecution.ExecutionStatus.CANCELLING,
            ]
            active = test_executions.filter(status__in=non_deletable_statuses)
            if active.exists():
                active_ids = list(active.values_list("id", flat=True))
                return self._gm.bad_request(
                    f"Cannot delete test executions that are currently active. "
                    f"Active IDs: {[str(id) for id in active_ids]}"
                )

            deleted_ids = list(
                str(id) for id in test_executions.values_list("id", flat=True)
            )
            count = len(deleted_ids)

            # Hard delete (cascades to call executions)
            test_executions.delete()

            logger.info(
                f"Bulk deleted {count} test executions from run test {run_test_id}"
            )

            return Response(
                {
                    "message": f"Successfully deleted {count} test execution(s).",
                    "run_test_id": str(run_test_id),
                    "deleted_count": count,
                    "deleted_ids": deleted_ids,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error in bulk test execution delete: {str(e)}")
            traceback.print_exc()
            return Response(
                {"error": "Failed to delete test executions"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CallExecutionDeleteView(APIView):
    """
    API View to delete a specific call execution
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request, call_execution_id, *args, **kwargs):
        """
        Delete a specific call execution
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__run_test__organization=user_organization,
                deleted=False,
            )

            # Soft delete the call execution
            call_execution.deleted = True
            call_execution.deleted_at = timezone.now()
            call_execution.save()

            return Response(
                {"message": "Call execution deleted successfully"},
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to delete call execution: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestDeleteView(APIView):
    """
    API View to delete a specific run test
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def delete(self, request, run_test_id, *args, **kwargs):
        """Delete a specific run test"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Check if run test has any running test executions
            running_executions = TestExecution.objects.filter(
                run_test=run_test, status=TestExecution.ExecutionStatus.RUNNING
            ).exists()

            if running_executions:
                return self.gm.bad_request(
                    "Cannot delete a run test that has test executions currently running."
                )

            # Soft delete the run test
            run_test.delete()  # This calls the custom delete method that sets deleted=True

            return Response(
                {"message": "Run test deleted successfully"}, status=status.HTTP_200_OK
            )

        except RunTest.DoesNotExist:
            return Response(
                {"error": "Run test not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to delete run test: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestComponentsUpdateView(APIView):
    """
    API View to update components of a run test (AgentDefinition, SimulatorAgent, Scenarios)
    with scenarios replacement - maintains only the specified scenarios as final state
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def patch(self, request, run_test_id, *args, **kwargs):
        """Update components of a specific RunTest"""
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            with transaction.atomic():
                # Track if agent version will be changed
                agent_version_changed = False
                new_agent_version = None

                # Update AgentDefinition if provided
                if "agent_definition_id" in request.data:
                    agent_definition_id = request.data["agent_definition_id"]
                    try:
                        agent_definition = AgentDefinition.objects.get(
                            id=agent_definition_id,
                            organization=user_organization,
                            deleted=False,
                        )
                        run_test.agent_definition = agent_definition
                        run_test.agent_version = agent_definition.latest_version
                        new_agent_version = agent_definition.latest_version
                        agent_version_changed = True
                    except AgentDefinition.DoesNotExist:
                        return Response(
                            {"error": "Agent definition not found"},
                            status=status.HTTP_404_NOT_FOUND,
                        )

                if "version" in request.data:
                    version_id = request.data["version"]
                    try:
                        version = AgentVersion.objects.get(
                            id=version_id, organization=user_organization, deleted=False
                        )
                        run_test.agent_version = version
                        new_agent_version = version
                        agent_version_changed = True
                    except AgentVersion.DoesNotExist:
                        return Response(
                            {"error": "Agent version not found"},
                            status=status.HTTP_404_NOT_FOUND,
                        )

                # Update SimulatorAgent if provided
                if "simulator_agent_id" in request.data:
                    simulator_agent_id = request.data["simulator_agent_id"]
                    try:
                        simulator_agent = SimulatorAgent.objects.get(
                            id=simulator_agent_id,
                            organization=user_organization,
                            deleted=False,
                        )
                        run_test.simulator_agent = simulator_agent
                    except SimulatorAgent.DoesNotExist:
                        return Response(
                            {"error": "Simulator agent not found"},
                            status=status.HTTP_404_NOT_FOUND,
                        )

                # Handle scenarios - replace with new list to maintain only specified scenarios as final state
                if "scenarios" in request.data:
                    scenario_ids = request.data["scenarios"]

                    # Validate scenarios data structure
                    if not isinstance(scenario_ids, list):
                        return self.gm.bad_request(
                            "Scenarios must be provided as an array of scenario IDs"
                        )

                    # Validate that all scenarios exist and belong to the organization
                    scenarios_to_set = Scenarios.objects.filter(
                        id__in=scenario_ids,
                        organization=user_organization,
                        deleted=False,
                    )

                    if len(scenarios_to_set) != len(scenario_ids):
                        found_ids = set(scenarios_to_set.values_list("id", flat=True))
                        missing_ids = set(scenario_ids) - found_ids
                        return Response(
                            {"error": f"Scenarios not found: {list(missing_ids)}"},
                            status=status.HTTP_404_NOT_FOUND,
                        )

                    # Replace all scenarios with the new list to maintain only specified scenarios as final state
                    run_test.scenarios.set(scenarios_to_set)

                # Update enable_tool_evaluation if provided
                if "enable_tool_evaluation" in request.data:
                    run_test.enable_tool_evaluation = request.data[
                        "enable_tool_evaluation"
                    ]

                # Validate that if tool evaluation is enabled, agent has required api_key and assistant_id
                if run_test.enable_tool_evaluation:
                    # Determine which agent version to check
                    agent_version_to_check = (
                        new_agent_version
                        if agent_version_changed
                        else run_test.agent_version
                    )

                    if agent_version_to_check and run_test.agent_definition:
                        agent_type = run_test.agent_definition.agent_type
                        if (
                            not agent_type
                            or agent_type == AgentDefinition.AgentTypeChoices.VOICE
                        ):
                            # Check configuration_snapshot for api_key and assistant_id
                            config_snapshot = (
                                agent_version_to_check.configuration_snapshot or {}
                            )
                            api_key = config_snapshot.get("api_key")
                            assistant_id = config_snapshot.get("assistant_id")

                            missing_fields = []
                            if not api_key or (
                                isinstance(api_key, str) and not api_key.strip()
                            ):
                                missing_fields.append("api_key")
                            if not assistant_id or (
                                isinstance(assistant_id, str)
                                and not assistant_id.strip()
                            ):
                                missing_fields.append("assistant_id")

                            if missing_fields:
                                return self.gm.bad_request(
                                    {
                                        "error_code": "API_KEY_AND_ASSISTANT_ID_REQUIRED",
                                        "error_message": f"Tool evaluation requires agent configuration to have: {', '.join(missing_fields)}. Please update the agent definition with these fields before enabling tool evaluation.",
                                    }
                                )
                    else:
                        return self.gm.bad_request(
                            {
                                "error_code": "API_KEY_AND_ASSISTANT_ID_REQUIRED",
                                "error_message": "Tool evaluation requires an agent version to be set. Please specify an agent definition or version before enabling tool evaluation.",
                            }
                        )

                # Save the run test
                run_test.save()

                # Serialize and return the updated run test
                response_serializer = RunTestSerializer(run_test)
                return Response(response_serializer.data, status=status.HTTP_200_OK)

        except RunTest.DoesNotExist:
            traceback.print_exc()
            return Response(
                {"error": "Run test not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to update run test components: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CallExecutionErrorLocalizerTasksView(APIView):
    """
    API View to retrieve error localizer tasks for a specific call execution
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, call_execution_id, *args, **kwargs):
        """Get error localizer tasks for a specific call execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get eval_config_id from query parameters
            eval_config_id = request.query_params.get("eval_config_id")

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__run_test__organization=user_organization,
                test_execution__run_test__deleted=False,
            )

            # Find error localizer tasks for this call execution
            # Filter by source_id (call_execution_id) and eval_config_id if provided
            query_filter = {
                "source": ErrorLocalizerSource.SIMULATE,
                "source_id": call_execution.id,
            }

            # If eval_config_id is provided, filter by it in metadata
            if eval_config_id:
                query_filter["metadata__eval_config_id"] = str(eval_config_id)

            call_execution_task = (
                ErrorLocalizerTask.no_workspace_objects.filter(**query_filter)
                .order_by("-created_at")
                .first()
            )

            error_localizer_data = []
            if call_execution_task:
                # Extract eval_config_id from metadata
                task_eval_config_id = call_execution_task.metadata.get("eval_config_id")

                # Normalize status: running -> "running", completed -> "completed", failed -> "FAILED", others -> ""
                normalized_status = ""
                if call_execution_task.status == ErrorLocalizerStatus.RUNNING:
                    normalized_status = "running"
                elif call_execution_task.status == ErrorLocalizerStatus.COMPLETED:
                    normalized_status = "completed"
                elif call_execution_task.status == ErrorLocalizerStatus.FAILED:
                    normalized_status = "failed"

                error_localizer_data.append(
                    {
                        "task_id": str(call_execution_task.id),
                        "eval_config_id": task_eval_config_id,
                        "status": normalized_status,
                        "eval_result": call_execution_task.eval_result,
                        "eval_explanation": call_execution_task.eval_explanation,
                        "input_data": call_execution_task.input_data,
                        "input_keys": call_execution_task.input_keys,
                        "input_types": call_execution_task.input_types,
                        "rule_prompt": call_execution_task.rule_prompt,
                        "error_analysis": call_execution_task.error_analysis,
                        "selected_input_key": call_execution_task.selected_input_key,
                        "error_message": call_execution_task.error_message,
                        "created_at": (
                            call_execution_task.created_at.isoformat()
                            if call_execution_task.created_at
                            else None
                        ),
                        "updated_at": (
                            call_execution_task.updated_at.isoformat()
                            if call_execution_task.updated_at
                            else None
                        ),
                        "eval_template_name": (
                            call_execution_task.eval_template.name
                            if call_execution_task.eval_template
                            else None
                        ),
                        "eval_template_id": (
                            str(call_execution_task.eval_template.id)
                            if call_execution_task.eval_template
                            else None
                        ),
                    }
                )

            return Response(
                {
                    "call_execution_id": str(call_execution.id),
                    "error_localizer_tasks": error_localizer_data,
                    "total_tasks": len(error_localizer_data),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve error localizer tasks: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestScenariosView(APIView):
    """
    API View to get paginated scenarios for a specific run test
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, run_test_id, *args, **kwargs):
        """
        Get paginated list of scenarios for a specific run test
        Query Parameters:
        - search: search string to filter scenarios by name
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test and verify it belongs to the user's organization
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Get search query parameter
            search_query = request.query_params.get("search", "").strip()

            # Get scenarios for this run test (only non-deleted scenarios)
            scenarios = run_test.scenarios.filter(deleted=False).select_related(
                "dataset"
            )

            # Apply search filter if search query is provided
            if search_query:
                # Create case-insensitive regex pattern for search
                pattern = rf"(?i){re.escape(search_query)}"
                scenarios = scenarios.filter(
                    models.Q(name__regex=pattern)
                    | models.Q(source__regex=pattern)
                    | models.Q(scenario_type__regex=pattern)
                )

            # Order by creation date (newest first)
            scenarios = scenarios.order_by("-created_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(scenarios, request)

            # Serialize the data with minimal fields for Bruno
            scenario_data = []
            for scenario in result_page:
                # Get the number of rows for dataset-type scenarios
                row_count = 0
                if scenario.scenario_type == "dataset" and scenario.dataset:
                    row_count = Row.objects.filter(
                        dataset=scenario.dataset, deleted=False
                    ).count()

                scenario_data.append(
                    {
                        "id": str(scenario.id),
                        "name": scenario.name,
                        "row_count": row_count,
                    }
                )

            # Return paginated response
            return paginator.get_paginated_response(scenario_data)

        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve scenarios: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AddEvalConfigView(APIView):
    """
    API View to add evaluation configs to a run test
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, run_test_id, *args, **kwargs):
        """
        Add evaluation configs to a run test
        Request Body:
        {
            "evaluations_config": [
                {
                    "template_id": "uuid",
                    "name": "Evaluation Name",
                    "config": {},
                    "mapping": {},
                    "filters": {},
                    "error_localizer": false,
                    "model": "gpt-4o-mini"
                }
            ]
        }
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test and verify it belongs to the user's organization
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Validate request data
            evaluations_config = request.data.get("evaluations_config", [])

            if not evaluations_config:
                return self.gm.bad_request(
                    "evaluations_config is required and cannot be empty"
                )

            # Check for duplicate names in the request
            names_in_request = []
            for eval_config_data in evaluations_config:
                template_id = eval_config_data.get("template_id")
                eval_name = eval_config_data.get("name")
                # Use default name if not provided
                if not eval_name and template_id:
                    eval_name = f"Eval-{template_id}"

                if eval_name:
                    if eval_name in names_in_request:
                        return self.gm.bad_request(
                            f"Duplicate eval name '{eval_name}' found in the request. Each evaluation config must have a unique name."
                        )
                    names_in_request.append(eval_name)

            # Get existing eval config names for this run test
            existing_eval_configs = SimulateEvalConfig.objects.filter(
                run_test=run_test, deleted=False
            )
            existing_names = {
                config.name for config in existing_eval_configs if config.name
            }

            created_eval_configs = []
            errors = []

            # Create SimulateEvalConfig instances from evaluations_config
            for eval_config_data in evaluations_config:
                try:
                    # Validate required fields
                    template_id = eval_config_data.get("template_id")
                    if not template_id:
                        errors.append(
                            "template_id is required for each evaluation config"
                        )
                        continue

                    # Get EvalTemplate by ID
                    try:
                        eval_template = EvalTemplate.no_workspace_objects.get(
                            Q(organization=user_organization)
                            | Q(organization__isnull=True),
                            id=template_id,
                        )
                    except EvalTemplate.DoesNotExist:
                        errors.append(f"EvalTemplate with id {template_id} not found")
                        continue

                    # Get the eval name (use default if not provided)
                    eval_name = eval_config_data.get("name")
                    if not eval_name:
                        eval_name = f"Eval-{template_id}"

                    # Check if name already exists in the run test
                    if eval_name in existing_names:
                        return self.gm.bad_request(
                            f"An evaluation config with the name '{eval_name}' already exists in this run test. Please use a different name."
                        )

                    # Create SimulateEvalConfig
                    simulate_eval = SimulateEvalConfig.objects.create(
                        eval_template=eval_template,
                        name=eval_name,
                        config=normalize_eval_runtime_config(
                            eval_template.config,
                            eval_config_data.get("config", {}),
                        ),
                        mapping=eval_config_data.get("mapping", {}),
                        run_test=run_test,
                        filters=eval_config_data.get("filters", {}),
                        error_localizer=eval_config_data.get("error_localizer", False),
                        model=eval_config_data.get("model", None),
                    )

                    # Add the new name to existing_names to prevent duplicates within the same request
                    existing_names.add(eval_name)
                    created_eval_configs.append(simulate_eval)

                except Exception as e:
                    errors.append(f"Error creating evaluation config: {str(e)}")
                    continue

            # Prepare response
            if created_eval_configs:
                # Serialize the created eval configs
                from simulate.serializers.run_test import (
                    SimulateEvalConfigSimpleSerializer,
                )

                eval_configs_data = SimulateEvalConfigSimpleSerializer(
                    created_eval_configs, many=True
                ).data

                response_data = {
                    "message": f"Successfully added {len(created_eval_configs)} evaluation config(s) to run test",
                    "created_eval_configs": eval_configs_data,
                    "run_test_id": str(run_test.id),
                }

                if errors:
                    response_data["warnings"] = errors

                return Response(response_data, status=status.HTTP_201_CREATED)
            else:
                return self.gm.bad_request("Failed to create any evaluation configs")

        except Exception as e:
            return Response(
                {"error": f"Failed to add evaluation configs: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeleteEvalConfigView(APIView):
    """
    API View to delete evaluation configs from a run test
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request, run_test_id, eval_config_id, *args, **kwargs):
        """
        Delete an evaluation config from a run test
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test and verify it belongs to the user's organization
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            with transaction.atomic():
                active_configs = SimulateEvalConfig.objects.select_for_update().filter(
                    run_test=run_test, deleted=False
                )
                # Ensure at least one eval config remains after deletion
                if active_configs.count() <= 1:
                    return Response(
                        {
                            "error": "Cannot delete the last evaluation config. At least one evaluation config must remain."
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Get the eval config and verify it belongs to the run test
                try:
                    eval_config = active_configs.get(id=eval_config_id)
                except SimulateEvalConfig.DoesNotExist:
                    return Response(
                        {"error": "Evaluation config not found"},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Soft delete the eval config
                eval_config.deleted = True
                eval_config.deleted_at = timezone.now()
                eval_config.save(update_fields=["deleted", "deleted_at"])

            return Response(
                {"message": "Evaluation config deleted successfully"},
                status=status.HTTP_200_OK,
            )

        except SimulateEvalConfig.DoesNotExist:
            return Response(
                {"error": "Evaluation config not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to delete evaluation config: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GetEvalConfigStructureView(APIView):
    """
    API View to get the structure of an evaluation config
    Similar to model_hub's GetEvalStructureView but for SimulateEvalConfig
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gm = GeneralMethods()

    def get(self, request, run_test_id, eval_config_id, *args, **kwargs):
        """
        Get the structure of an evaluation config
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test and verify it belongs to the user's organization
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Get the eval config and verify it belongs to the run test
            eval_config = get_object_or_404(
                SimulateEvalConfig,
                id=eval_config_id,
                run_test=run_test,
                deleted=False,
            )

            return self._get_previously_configured_structure(
                eval_config, user_organization
            )

        except Exception as e:
            logger.exception(f"Error in fetching eval config structure: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EVAL_STRUCTURE")
            )

    def _get_previously_configured_structure(self, eval_config, organization):
        """Get structure for a previously configured eval config"""
        try:
            template = eval_config.eval_template

            # Resolve once per request — the lookup is independent of
            # ``eval_config`` and should not live inside a per-config call
            # path where it could be re-executed.
            api_key_available = ApiKey.objects.filter(
                organization=organization, provider="openai"
            ).exists()

            # Build final_config with defaults from eval_config
            final_config = template.config.get("config", {})
            eval_config_config = (
                eval_config.config.get("config", {}) if eval_config.config else {}
            )
            function_params_schema, params = params_with_defaults_for_response(
                template.config, eval_config.config
            )
            for key in final_config:
                if key in eval_config_config:
                    final_config[key]["default"] = eval_config_config.get(key, "")

            # Build final_mapping from eval_config
            final_mapping = {}
            for key in template.config.get("required_keys", []):
                final_mapping[key] = (
                    eval_config.mapping.get(key, "") if eval_config.mapping else ""
                )

            eval_data = {
                "id": str(eval_config.id),
                "template_id": str(template.id),
                "name": eval_config.name,
                "reason_column": (
                    eval_config.config.get("reason_column", False)
                    if eval_config.config
                    else False
                ),
                "eval_tags": template.eval_tags,
                "description": template.description,
                "required_keys": template.config.get("required_keys", []),
                "optional_keys": template.config.get("optional_keys", []),
                "variable_keys": template.config.get("variable_keys", []),
                "run_prompt_column": template.config.get("run_prompt_column", False),
                "template_name": template.name,
                "mapping": final_mapping,
                "config": final_config,
                "params": params,
                "function_params_schema": function_params_schema,
                "models": template.config.get("models", ""),
                "selected_model": eval_config.model,
                "error_localizer": eval_config.error_localizer,
                "kb_id": str(eval_config.kb_id.id) if eval_config.kb_id else None,
                "output": template.config.get("output", ""),
                "config_params_desc": template.config.get("config_params_desc", {}),
                "config_params_option": strip_turing_from_config_options(
                    template.config.get("config_params_option", {})
                ),
                "api_key_available": api_key_available,
            }

            return self._gm.success_response({"eval": eval_data})

        except Exception as e:
            logger.exception(
                f"Error in getting previously configured structure: {str(e)}"
            )
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EVAL_STRUCTURE")
            )


class UpdateEvalConfigView(APIView):
    """
    API View to update an evaluation config with optional rerun
    Similar to model_hub's EditAndRunUserEvalView but for SimulateEvalConfig
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gm = GeneralMethods()

    def post(self, request, run_test_id, eval_config_id, *args, **kwargs):
        """
        Update an evaluation config and optionally trigger rerun

        Request Body:
        {
            "config": {
                "config": {...},
                "mapping": {...}
            },
            "mapping": {...},  # Can also be provided at top level
            "model": "gpt-4o-mini",
            "error_localizer": false,
            "kb_id": "uuid",
            "name": "Evaluation Name",
            "run": false,  # If true, trigger rerun on specified test execution
            "test_execution_id": "uuid"  # Required when run is true
        }
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test and verify it belongs to the user's organization
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Get the eval config and verify it belongs to the run test
            eval_config = get_object_or_404(
                SimulateEvalConfig,
                id=eval_config_id,
                run_test=run_test,
                deleted=False,
            )

            # Get run flag
            run = request.data.get("run", False)

            # Update config if provided (similar to EditAndRunUserEvalView)
            new_config = request.data.get("config")
            if new_config:
                eval_config.config = normalize_eval_runtime_config(
                    eval_config.eval_template.config, new_config
                )

            # Update mapping if provided at top level
            if "mapping" in request.data:
                eval_config.mapping = request.data.get("mapping")

            # Update other fields if provided
            if "name" in request.data:
                eval_config.name = request.data.get("name")
            if "model" in request.data:
                eval_config.model = request.data.get("model")
            if "error_localizer" in request.data:
                eval_config.error_localizer = request.data.get("error_localizer")
            if "kb_id" in request.data:
                kb_id = request.data.get("kb_id")
                if kb_id:
                    from model_hub.models.develop_dataset import KnowledgeBaseFile

                    try:
                        eval_config.kb_id = KnowledgeBaseFile.objects.get(
                            id=kb_id, organization=user_organization
                        )
                    except KnowledgeBaseFile.DoesNotExist:
                        return self._gm.bad_request("Knowledge base not found")
                else:
                    eval_config.kb_id = None

            # Save the eval config
            eval_config.save()

            # If run is True, trigger rerun on the specified test execution
            if run:
                # Get test_execution_id from request data
                test_execution_id = request.data.get("test_execution_id")
                if not test_execution_id:
                    return self._gm.bad_request(
                        "test_execution_id is required when run is true"
                    )

                # Get the specific test execution and verify it belongs to the run_test
                test_execution = get_object_or_404(
                    TestExecution,
                    id=test_execution_id,
                    run_test=run_test,
                )

                # Verify test execution is COMPLETED
                if test_execution.status not in [
                    TestExecution.ExecutionStatus.COMPLETED,
                    TestExecution.ExecutionStatus.CANCELLED,
                    TestExecution.ExecutionStatus.FAILED,
                ]:
                    return self._gm.bad_request(
                        "Only test executions with COMPLETED, CANCELLED, or FAILED status can have evaluations rerun"
                    )

                # Get all call executions from this test execution
                call_executions = CallExecution.objects.filter(
                    test_execution=test_execution
                )

                if not call_executions.exists():
                    return self._gm.success_response(
                        {
                            "message": "Evaluation config updated successfully. No call executions found to rerun.",
                            "eval_config_id": str(eval_config.id),
                            "run_test_id": str(run_test_id),
                            "test_execution_id": str(test_execution_id),
                        }
                    )

                call_execution_ids = [str(ce.id) for ce in call_executions]
                eval_config_ids_str = [str(eval_config.id)]

                # Bulk update eval_started flag and initialize eval_outputs for all call executions
                call_executions_to_update = CallExecution.objects.filter(
                    id__in=call_execution_ids
                )
                call_executions_list = []
                for call_execution in call_executions_to_update:
                    # Provider-agnostic eval flags live in call_metadata
                    call_execution.call_metadata = call_execution.call_metadata or {}
                    call_execution.call_metadata["eval_started"] = True
                    call_execution.call_metadata["eval_completed"] = False

                    # Initialize eval_outputs for the updated eval config
                    if not call_execution.eval_outputs:
                        call_execution.eval_outputs = {}

                    # Set placeholder values for the eval config that will be rerun
                    call_execution.eval_outputs[str(eval_config.id)] = {
                        "status": "pending"
                    }

                    call_executions_list.append(call_execution)

                if call_executions_list:
                    CallExecution.objects.bulk_update(
                        call_executions_list, ["call_metadata", "eval_outputs"]
                    )
                    logger.info(
                        f"Bulk updated eval_started flag and initialized eval_outputs for "
                        f"{len(call_executions_list)} call executions with eval config {eval_config.id}"
                    )

                # Update test execution status to EVALUATING
                test_execution.status = TestExecution.ExecutionStatus.EVALUATING
                test_execution.picked_up_by_executor = False
                test_execution.save(update_fields=["status", "picked_up_by_executor"])

                # Trigger the Celery task to run evaluations asynchronously
                task = run_new_evals_on_call_executions_task.apply_async(
                    args=(call_execution_ids, eval_config_ids_str),
                )

                logger.info(
                    f"Triggered rerun evaluations task {task.id} for {len(call_execution_ids)} call executions "
                    f"in test execution {test_execution_id} with eval config {eval_config.id}"
                )

                return Response(
                    {
                        "message": "Evaluation config updated and rerun triggered successfully",
                        "eval_config_id": str(eval_config.id),
                        "run_test_id": str(run_test_id),
                        "test_execution_id": str(test_execution_id),
                        "call_execution_count": len(call_execution_ids),
                        "note": f"{len(call_execution_ids)} parallel tasks will be spawned to process evaluations",
                    },
                    status=status.HTTP_200_OK,
                )

            return self._gm.success_response(
                {
                    "message": "Evaluation config updated successfully",
                    "eval_config_id": str(eval_config.id),
                    "run_test_id": str(run_test_id),
                }
            )

        except Exception as e:
            logger.exception(f"Error in updating the evaluation config: {str(e)}")
            traceback.print_exc()
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_EVALUATION_AND_PROCESS")
            )


class RunTestExecutionsView(APIView):
    """
    API View to get test execution data for a specific run test with search and pagination
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, run_test_id, *args, **kwargs):
        """
        Get test execution data for a specific run test
        Query Parameters:
        - search: search string to filter test executions by status or scenario name
        - status: filter by execution status
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test and verify it belongs to the user's organization
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Get query parameters
            search_query = request.query_params.get("search", "").strip()
            status_filter = request.query_params.get("status", "").strip()

            # Get test executions for this run test with annotations to avoid N+1 queries
            # Use annotations to calculate call metrics in a single query
            test_executions = (
                TestExecution.objects.filter(run_test=run_test, deleted=False)
                .select_related(
                    "agent_definition",
                    "agent_version",
                )
                .annotate(
                    _total_calls=Count("calls"),
                    _completed_calls=Count(
                        "calls",
                        filter=models.Q(
                            calls__status=CallExecution.CallStatus.COMPLETED
                        ),
                    ),
                    _pending_calls=Count(
                        "calls",
                        filter=models.Q(calls__status=CallExecution.CallStatus.PENDING),
                    ),
                    _queued_calls=Count(
                        "calls",
                        filter=models.Q(
                            calls__status=CallExecution.CallStatus.REGISTERED
                        ),
                    ),
                    _connected_calls=Count(
                        "calls",
                        filter=models.Q(calls__duration_seconds__gt=0),
                    ),
                    _avg_response_time_ms=Avg("calls__response_time_ms"),
                )
            )

            # Apply search filter if search query is provided
            if search_query:
                pattern = rf"(?i){re.escape(search_query)}"

                # Search in status
                status_filtered_ids = test_executions.filter(
                    models.Q(status__regex=pattern)
                ).values_list("id", flat=True)

                # Search in scenario names by getting scenario IDs and filtering
                matching_scenario_ids = set(
                    str(sid)
                    for sid in Scenarios.objects.filter(
                        name__regex=pattern, deleted=False
                    ).values_list("id", flat=True)
                )

                # Get test executions that have any of the matching scenarios in their scenario_ids
                # Use a more efficient approach - filter in DB where possible
                scenario_filtered_ids = []
                if matching_scenario_ids:
                    # Only iterate over executions if we have matching scenarios
                    for execution in test_executions.only("id", "scenario_ids"):
                        if execution.scenario_ids and any(
                            str(scenario_id) in matching_scenario_ids
                            for scenario_id in execution.scenario_ids
                        ):
                            scenario_filtered_ids.append(execution.id)

                # Combine both filter results
                combined_ids = list(
                    set(list(status_filtered_ids) + scenario_filtered_ids)
                )
                test_executions = test_executions.filter(id__in=combined_ids)

            # Apply status filter if provided
            if status_filter:
                test_executions = test_executions.filter(status=status_filter)

            # Order by creation date (newest first)
            test_executions = test_executions.order_by("-created_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(test_executions, request)

            # Batch fetch scenario names for all executions in one query
            all_scenario_ids = set()
            for te in result_page:
                if te.scenario_ids:
                    all_scenario_ids.update(te.scenario_ids)

            scenario_names_map = {}
            if all_scenario_ids:
                scenarios_qs = Scenarios.objects.filter(
                    id__in=all_scenario_ids, deleted=False
                ).values("id", "name")
                scenario_names_map = {str(s["id"]): s["name"] for s in scenarios_qs}

            # Batch fetch agent turn counts for all executions in one query
            execution_ids = [te.id for te in result_page]
            agent_turn_counts = {}
            chat_duration_map = {}
            if execution_ids:
                turn_counts_qs = (
                    ChatMessageModel.objects.filter(
                        call_execution__test_execution_id__in=execution_ids,
                        role=ChatMessageModel.RoleChoices.USER,
                    )
                    .values("call_execution__test_execution_id")
                    .annotate(count=Count("id"))
                )
                agent_turn_counts = {
                    str(tc["call_execution__test_execution_id"]): tc["count"]
                    for tc in turn_counts_qs
                }

                # Batch fetch chat duration (first→last message) for prompt-based sims
                if run_test.source_type == RunTest.SourceTypes.PROMPT:
                    from django.db.models import Max, Min

                    chat_times_qs = (
                        ChatMessageModel.objects.filter(
                            call_execution__test_execution_id__in=execution_ids,
                        )
                        .values("call_execution__test_execution_id")
                        .annotate(
                            first_msg=Min("created_at"),
                            last_msg=Max("created_at"),
                        )
                    )
                    for ct in chat_times_qs:
                        te_id = str(ct["call_execution__test_execution_id"])
                        if ct["first_msg"] and ct["last_msg"]:
                            delta = ct["last_msg"] - ct["first_msg"]
                            chat_duration_map[te_id] = int(delta.total_seconds())
                        else:
                            chat_duration_map[te_id] = 0

            # Prepare response data using annotated values
            execution_data = []

            for test_execution in result_page:
                # Use annotated values instead of querying
                # For prompt-based sims, use chat message timestamps (covers old data without duration_seconds)
                if run_test.source_type == RunTest.SourceTypes.PROMPT:
                    duration = chat_duration_map.get(
                        str(test_execution.id),
                        test_execution.duration_seconds or 0,
                    )
                else:
                    duration = test_execution.duration_seconds or 0
                total_calls = test_execution._total_calls or 0
                completed_calls = test_execution._completed_calls or 0
                pending_calls = test_execution._pending_calls or 0
                queued_calls = test_execution._queued_calls or 0
                connected_calls = test_execution._connected_calls or 0
                avg_response_time_ms = test_execution._avg_response_time_ms

                # Calculate derived metrics
                success_rate = (
                    round((completed_calls / total_calls * 100), 1)
                    if total_calls > 0
                    else 0
                )

                avg_response_time = 0.0
                if avg_response_time_ms is not None:
                    avg_response_time = avg_response_time_ms / 1000

                # Use batch-fetched scenario names
                scenario_names = []
                if test_execution.scenario_ids:
                    scenario_names = [
                        scenario_names_map.get(str(sid), "")
                        for sid in test_execution.scenario_ids
                        if str(sid) in scenario_names_map
                    ]
                scenarios_text = (
                    ", ".join(scenario_names) if scenario_names else "No scenarios"
                )

                # Calculate call metrics from annotated values
                execution_calls_attempted = total_calls - pending_calls - queued_calls
                execution_calls_connected_percentage = (
                    round((connected_calls / execution_calls_attempted * 100), 2)
                    if execution_calls_attempted > 0
                    else 0.0
                )

                # Handle both agent-based and prompt-based simulations
                # agent_definition and agent_version are already select_related
                agent_definition = test_execution.agent_definition
                agent_version = test_execution.agent_version

                # For prompt-based simulations, agent_definition is None
                if agent_definition or agent_version:
                    # Get agent name from the version's snapshot (historical) or agent_definition
                    if agent_version:
                        agent_version_name = agent_version.version_name
                        # Use snapshot for historical agent name, fallback to agent_definition
                        snapshot = agent_version.configuration_snapshot or {}
                        agent_definition_name = (
                            snapshot.get("agent_name")
                            or (
                                agent_version.agent_definition.agent_name
                                if agent_version.agent_definition
                                else None
                            )
                            or (
                                agent_definition.agent_name
                                if agent_definition
                                else "N/A"
                            )
                        )
                        agent_type = (
                            snapshot.get("agent_type")
                            or (
                                agent_version.agent_definition.agent_type
                                if agent_version.agent_definition
                                else None
                            )
                            or (
                                agent_definition.agent_type
                                if agent_definition
                                else AgentDefinition.AgentTypeChoices.VOICE
                            )
                        )
                    elif agent_definition:
                        # Fallback - use agent_definition directly (legacy executions)
                        agent_version_name = (
                            agent_definition.latest_version.version_name
                            if hasattr(agent_definition, "latest_version")
                            and agent_definition.latest_version
                            else "N/A"
                        )
                        agent_definition_name = agent_definition.agent_name
                        agent_type = agent_definition.agent_type
                    else:
                        agent_version_name = "N/A"
                        agent_definition_name = "N/A"
                        agent_type = AgentDefinition.AgentTypeChoices.VOICE

                    total_chats = (
                        total_calls
                        if agent_type == AgentDefinition.AgentTypeChoices.TEXT
                        else 0
                    )
                else:
                    # Prompt-based simulation
                    if run_test.prompt_version:
                        tv = str(run_test.prompt_version.template_version)
                        agent_version_name = tv if tv.startswith("v") else f"v{tv}"
                    else:
                        agent_version_name = "N/A"
                    agent_definition_name = (
                        run_test.prompt_template.name
                        if run_test.prompt_template
                        else "Prompt Simulation"
                    )
                    agent_type = "TEXT"  # Prompt-based simulations are always TEXT
                    total_chats = total_calls  # All calls are chats for prompt-based

                # Use batch-fetched agent turn count
                total_number_of_fagi_agent_turns = agent_turn_counts.get(
                    str(test_execution.id), 0
                )

                execution_data.append(
                    {
                        "id": str(test_execution.id),
                        "status": test_execution.status.title(),
                        "scenarios": scenarios_text,
                        "start_time": (
                            test_execution.started_at.isoformat()
                            if test_execution.started_at
                            else None
                        ),
                        "duration": int(duration),
                        "error_reason": test_execution.error_reason,
                        "success_rate": round(success_rate, 2),
                        "avg_response_time": round(avg_response_time, 3),
                        "calls": test_execution.total_calls,
                        "calls_attempted": execution_calls_attempted,
                        "connected_calls": connected_calls,
                        "agent_version": agent_version_name,
                        "agent_definition": agent_definition_name,
                        "calls_connected_percentage": execution_calls_connected_percentage,
                        "total_chats": total_chats,
                        "agent_type": agent_type,
                        "total_number_of_fagi_agent_turns": total_number_of_fagi_agent_turns,
                        "source_type": run_test.source_type,
                    }
                )

            # Return paginated response with execution data
            return paginator.get_paginated_response(execution_data)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to retrieve test executions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CSVExportView(APIView):
    """
    API View to export data as CSV - supports both RunTest and TestExecution
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, item_id, *args, **kwargs):
        """
        Export data as CSV based on type parameter
        Query Parameters:
        - type: 'runtest' or 'testexecution' (required)
        - search: search string to filter call executions by phone number or scenario name
        - status: filter by call execution status
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the type parameter
            export_type = request.query_params.get("type", "").lower().strip()

            if not export_type:
                return self.gm.bad_request(
                    "Type parameter is required. Use 'runtest' or 'testexecution'"
                )

            if export_type not in ["runtest", "testexecution"]:
                return self.gm.bad_request(
                    "Invalid type. Use 'runtest' or 'testexecution'"
                )

            # Get query parameters for filtering
            search_query = request.query_params.get("search", "").strip()
            status_filter = request.query_params.get("status", "").strip()

            if export_type == "runtest":
                # Export RunTest data
                run_test = get_object_or_404(
                    RunTest, id=item_id, organization=user_organization, deleted=False
                )

                # Get all call executions for all test executions of this run test
                call_executions = CallExecution.objects.filter(
                    test_execution__run_test=run_test
                ).select_related("scenario", "test_execution")

                filename = (
                    f"runtest_{item_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
                )

            else:  # testexecution
                # Export TestExecution data
                test_execution = get_object_or_404(
                    TestExecution,
                    id=item_id,
                    run_test__organization=user_organization,
                    run_test__deleted=False,
                )

                # Get all call executions for this test execution
                call_executions = CallExecution.objects.filter(
                    test_execution=test_execution
                ).select_related("scenario", "test_execution")

                filename = f"testexecution_{item_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"

            # Apply search filter if search query is provided
            if search_query:
                pattern = rf"(?i){re.escape(search_query)}"
                call_executions = call_executions.filter(
                    models.Q(phone_number__regex=pattern)
                    | models.Q(scenario__name__regex=pattern)
                )

            # Apply status filter if provided
            if status_filter:
                call_executions = call_executions.filter(status=status_filter)

            # Order by updated date (newest/most recently rerun first)
            call_executions = call_executions.order_by("-updated_at")

            # Create a CSV response
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'

            # Get all unique evaluation names from eval_outputs to create dynamic columns
            eval_columns = set()
            for call_execution in call_executions:
                if call_execution.eval_outputs:
                    for (
                        eval_config_id,
                        eval_data,
                    ) in call_execution.eval_outputs.items():
                        eval_name = eval_data.get("name", f"Eval_{eval_config_id}")
                        eval_columns.add(eval_name)

            # Get all unique tool output names from tool_outputs to create dynamic columns
            tool_columns = set()
            for call_execution in call_executions:
                if call_execution.tool_outputs:
                    for (
                        tool_eval_id,
                        tool_data,
                    ) in call_execution.tool_outputs.items():
                        tool_name = tool_data.get("name", f"Tool_{tool_eval_id}")
                        tool_columns.add(tool_name)

            # Define CSV headers with dynamic evaluation columns
            fieldnames = [
                "ID",
                "Timestamp",
                "Call Type",
                "Status",
                "Duration",
                "Scenario",
                "Overall Score",
                "Response Time",
                "Audio URL",
                "Provider call ID",
            ]

            # Add evaluation columns and their reason columns in sorted order
            sorted_eval_columns = sorted(eval_columns)
            for eval_name in sorted_eval_columns:
                fieldnames.append(eval_name)
                fieldnames.append(f"{eval_name}_reason")

            # Add tool output columns (name and reason) in sorted order
            sorted_tool_columns = sorted(tool_columns)
            for tool_name in sorted_tool_columns:
                fieldnames.append(tool_name)
                fieldnames.append(f"{tool_name}_reason")

            # Create a CSV writer
            writer = csv.DictWriter(response, fieldnames=fieldnames)

            # Write the header
            writer.writeheader()

            # Write data rows
            for call_execution in call_executions:
                # Calculate duration
                duration = None
                duration = call_execution.duration_seconds

                # Calculate response time in seconds
                response_time = None
                if call_execution.response_time_ms is not None:
                    response_time = round(call_execution.response_time_ms / 1000, 3)

                # Determine call type
                call_type = "Outbound"  # Default
                if call_execution.call_type:
                    if "outbound" in call_execution.call_type.lower():
                        call_type = "Outbound"
                    elif "inbound" in call_execution.call_type.lower():
                        call_type = "Inbound"

                # Start with base row data
                row_data = {
                    "ID": str(call_execution.id),
                    "Timestamp": (
                        call_execution.created_at.isoformat()
                        if call_execution.created_at
                        else ""
                    ),
                    "Call Type": call_type,
                    "Status": call_execution.status,
                    "Duration": duration or "",
                    "Scenario": call_execution.scenario.name,
                    "Overall Score": call_execution.overall_score or "",
                    "Response Time": response_time or "",
                    "Audio URL": call_execution.recording_url or "",
                    "Provider call ID": call_execution.customer_call_id or "",
                    # 'Customer Name': call_execution.customer_number or ''
                }

                # Initialize all evaluation columns and reasons with empty values
                for eval_name in eval_columns:
                    row_data[eval_name] = ""
                    row_data[f"{eval_name}_reason"] = ""

                # Add evaluation outputs and reasons as separate columns
                if call_execution.eval_outputs:
                    for (
                        eval_config_id,
                        eval_data,
                    ) in call_execution.eval_outputs.items():
                        eval_name = eval_data.get("name", f"Eval_{eval_config_id}")
                        eval_output = eval_data.get("output", "")
                        eval_reason = eval_data.get("reason", "")
                        row_data[eval_name] = str(eval_output)
                        row_data[f"{eval_name}_reason"] = str(eval_reason)

                # Initialize all tool output columns with empty values
                for tool_name in tool_columns:
                    row_data[tool_name] = ""
                    row_data[f"{tool_name}_reason"] = ""

                # Add tool outputs (name and reason) as separate columns
                if call_execution.tool_outputs:
                    for (
                        tool_eval_id,
                        tool_data,
                    ) in call_execution.tool_outputs.items():
                        tool_name = tool_data.get("name", f"Tool_{tool_eval_id}")
                        tool_value = tool_data.get("value", "")
                        tool_reason = tool_data.get("reason", "")
                        row_data[tool_name] = str(tool_value)
                        row_data[f"{tool_name}_reason"] = str(tool_reason)

                writer.writerow(row_data)

            return response

        except Exception as e:
            return Response(
                {"error": f"Failed to export data: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunTestEvalSummaryView(APIView):
    """
    API View to get evaluation summary statistics for a single run test
    Supports both single execution (GET) and multiple execution comparison (POST)
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request, run_test_id, *args, **kwargs):
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            execution_id = request.query_params.get("execution_id", None)

            run_test = RunTest.objects.get(
                id=run_test_id, organization=user_organization
            )
            eval_configs = _get_eval_configs_with_template(run_test)

            if not eval_configs:
                return Response([], status=status.HTTP_200_OK)

            call_executions = _get_completed_call_executions(run_test, execution_id)
            template_stats = _build_template_statistics(eval_configs, call_executions)
            final_data = _calculate_final_template_summaries(template_stats)

            return self._gm.success_response(final_data)

        except Exception:
            print(traceback.format_exc())
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_EVAL_SUMMARY")
            )


class RunTestEvalSummaryComparisonView(APIView):
    """
    API View to get comparison evaluation summary statistics for a single run test
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request, run_test_id, *args, **kwargs):
        """
        Compare evaluation summary statistics across multiple executions for a single run test
        """
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            execution_ids_param = request.GET.get("execution_ids", "[]")

            try:
                execution_ids = json.loads(execution_ids_param)
            except json.JSONDecodeError:
                return self._gm.bad_request_response("execution_ids must be valid JSON")

            if not execution_ids:
                return self._gm.bad_request_response("execution_ids list is required")

            run_test = RunTest.objects.get(
                id=run_test_id, organization=user_organization
            )

            eval_configs = _get_eval_configs_with_template(run_test)

            if not eval_configs:
                return Response({}, status=status.HTTP_200_OK)

            comparison_results = {}

            for execution_id in execution_ids:
                call_executions = _get_completed_call_executions(run_test, execution_id)
                template_stats = _build_template_statistics(
                    eval_configs, call_executions
                )
                final_data = _calculate_final_template_summaries(template_stats)
                comparison_results[str(execution_id)] = final_data

            return self._gm.success_response(comparison_results)

        except Exception:
            print(traceback.format_exc())
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_EVAL_SUMMARY")
            )


class RunTestEvalExplanationSummaryView(APIView):
    """
    API View to get evaluation explanation summary statistics for a single test execution.

    GET: Fetches stored summary from DB, triggers async calculation if not present
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request, test_execution_id, *args, **kwargs):
        """
        Fetch the evaluation explanation summary from the database.
        If not present, trigger async calculation and return empty response.
        """
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            test_execution = TestExecution.objects.get(
                id=test_execution_id, run_test__organization=organization
            )

            if test_execution.eval_explanation_summary is None:
                run_eval_summary_task.apply_async(args=(str(test_execution.id),))
                test_execution.eval_explanation_summary_status = (
                    EvalExplanationSummaryStatus.PENDING
                )
                test_execution.save(update_fields=["eval_explanation_summary_status"])

            return self._gm.success_response(
                {
                    "response": test_execution.eval_explanation_summary,
                    "last_updated": test_execution.eval_explanation_summary_last_updated,
                    "status": test_execution.eval_explanation_summary_status,
                }
            )

        except TestExecution.DoesNotExist:
            return self._gm.not_found_response(
                get_error_message("TEST_EXECUTION_NOT_FOUND")
            )
        except Exception:
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_EVAL_REASON_SUMMARY")
            )


class RunTestEvalExplanationSummaryRefreshView(APIView):
    """
    API View to refresh evaluation explanation summary by recalculating it.

    POST: Triggers async recalculation of the summary
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request, test_execution_id, *args, **kwargs):
        """
        Refresh the evaluation explanation summary by recalculating it.
        This endpoint triggers the summary calculation task again.
        """
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            test_execution = TestExecution.objects.get(
                id=test_execution_id, run_test__organization=organization
            )
            test_execution.eval_explanation_summary_status = (
                EvalExplanationSummaryStatus.PENDING
            )
            test_execution.save(update_fields=["eval_explanation_summary_status"])
            run_eval_summary_task.apply_async(args=(str(test_execution.id),))

            return self._gm.success_response(
                {"message": "Summary refresh initiated successfully"}
            )

        except TestExecution.DoesNotExist:
            return self._gm.not_found_response(
                get_error_message("TEST_EXECUTION_NOT_FOUND")
            )
        except Exception:
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_REFRESH_EVAL_REASON_SUMMARY")
            )


class TestExecutionOptimiserAnalysisView(APIView):
    """
    API View to get agent optimiser analysis for a test execution.

    GET: Fetches the latest optimiser analysis result
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request, test_execution_id, *args, **kwargs):
        """
        Fetch the agent optimiser analysis for a test execution.
        If not present or pending, returns status information.
        """
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            test_execution = TestExecution.objects.get(
                id=test_execution_id, run_test__organization=organization
            )

            optimiser = get_or_create_optimiser_for_test_execution(test_execution)

            result_data = get_latest_optimiser_result(optimiser, test_execution)

            return self._gm.success_response(result_data)

        except TestExecution.DoesNotExist:
            return self._gm.not_found_response(
                get_error_message("TEST_EXECUTION_NOT_FOUND")
            )
        except Exception:
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_OPTIMISER_ANALYSIS")
            )


class TestExecutionOptimiserAnalysisRefreshView(APIView):
    """
    API View to refresh agent optimiser analysis by triggering a new run.

    POST: Triggers a new optimiser analysis run
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request, test_execution_id, *args, **kwargs):
        """
        Trigger a new agent optimiser analysis run.
        """
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            test_execution = TestExecution.objects.get(
                id=test_execution_id, run_test__organization=organization
            )

            optimiser = get_or_create_optimiser_for_test_execution(test_execution)

            run = create_optimiser_run_for_test_execution(test_execution, optimiser)

            if run:
                logger.info(
                    f"Created optimiser analysis run {run.id} for test execution {test_execution_id}"
                )

                return self._gm.success_response(
                    {
                        "message": "Optimiser analysis refresh initiated successfully",
                        "status": run.status,
                    }
                )
            else:
                return self._gm.bad_request_response(
                    "Unable to prepare input data. Ensure test execution has completed calls."
                )

        except TestExecution.DoesNotExist:
            return self._gm.not_found_response(
                get_error_message("TEST_EXECUTION_NOT_FOUND")
            )
        except Exception:
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_REFRESH_OPTIMISER_ANALYSIS")
            )


def _clear_call_execution_data(call_execution):
    """Clear all call execution data for rerun"""

    # Create snapshot of current state before clearing
    CallExecutionSnapshot.objects.create(
        call_execution=call_execution,
        rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        service_provider_call_id=call_execution.service_provider_call_id,
        status=call_execution.status,
        started_at=call_execution.started_at,
        completed_at=call_execution.completed_at,
        ended_at=call_execution.ended_at,
        duration_seconds=call_execution.duration_seconds,
        recording_url=call_execution.recording_url,
        stereo_recording_url=call_execution.stereo_recording_url,
        cost_cents=call_execution.cost_cents,
        stt_cost_cents=call_execution.stt_cost_cents,
        llm_cost_cents=call_execution.llm_cost_cents,
        tts_cost_cents=call_execution.tts_cost_cents,
        vapi_cost_cents=call_execution.vapi_cost_cents,
        call_summary=call_execution.call_summary,
        ended_reason=call_execution.ended_reason,
        overall_score=call_execution.overall_score,
        response_time_ms=call_execution.response_time_ms,
        assistant_id=call_execution.assistant_id,
        customer_number=call_execution.customer_number,
        call_type=call_execution.call_type,
        analysis_data=call_execution.analysis_data,
        evaluation_data=call_execution.evaluation_data,
        message_count=call_execution.message_count,
        transcript_available=call_execution.transcript_available,
        recording_available=call_execution.recording_available,
        eval_outputs=call_execution.eval_outputs,
        tool_outputs=call_execution.tool_outputs,
        provider_call_data=call_execution.provider_call_data,
        monitor_call_data=call_execution.monitor_call_data,
        avg_agent_latency_ms=call_execution.avg_agent_latency_ms,
        user_interruption_count=call_execution.user_interruption_count,
        user_interruption_rate=call_execution.user_interruption_rate,
        user_wpm=call_execution.user_wpm,
        bot_wpm=call_execution.bot_wpm,
        talk_ratio=call_execution.talk_ratio,
        ai_interruption_count=call_execution.ai_interruption_count,
        ai_interruption_rate=call_execution.ai_interruption_rate,
        avg_stop_time_after_interruption_ms=call_execution.avg_stop_time_after_interruption_ms,
        conversation_metrics_data=call_execution.conversation_metrics_data,
        transcripts=list(
            call_execution.transcripts.values(
                "speaker_role",
                "content",
                "start_time_ms",
                "end_time_ms",
                "confidence_score",
            )
        ),
    )

    # Clear existing transcripts
    call_execution.transcripts.all().delete()

    call_execution.service_provider_call_id = None
    call_execution.monitor_call_data = None
    call_execution.status = CallExecution.CallStatus.PENDING
    call_execution.started_at = None
    call_execution.completed_at = None
    call_execution.ended_at = None
    call_execution.duration_seconds = None
    call_execution.recording_url = None
    call_execution.stereo_recording_url = None
    call_execution.cost_cents = None
    call_execution.stt_cost_cents = None
    call_execution.llm_cost_cents = None
    call_execution.tts_cost_cents = None
    call_execution.vapi_cost_cents = None
    call_execution.call_summary = None
    call_execution.ended_reason = None
    call_execution.overall_score = None
    call_execution.response_time_ms = None
    call_execution.assistant_id = None
    call_execution.customer_number = None
    call_execution.call_type = None
    call_execution.analysis_data = None
    call_execution.evaluation_data = None
    call_execution.message_count = None
    call_execution.transcript_available = False
    call_execution.recording_available = False
    call_execution.eval_outputs = {}
    call_execution.tool_outputs = {}
    call_execution.provider_call_data = {}
    call_execution.avg_agent_latency_ms = None
    call_execution.user_interruption_count = None
    call_execution.user_interruption_rate = None
    call_execution.user_wpm = None
    call_execution.bot_wpm = None
    call_execution.talk_ratio = None
    call_execution.ai_interruption_count = None
    call_execution.ai_interruption_rate = None
    call_execution.avg_stop_time_after_interruption_ms = None
    call_execution.conversation_metrics_data = None
    call_execution.call_metadata = {}
    call_execution.save()


def _save_eval_snapshot(call_execution):
    """Save a snapshot of evaluation data only (for eval_only reruns)"""

    # Create snapshot with only evaluation data
    CallExecutionSnapshot.objects.create(
        call_execution=call_execution,
        rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
        eval_outputs=call_execution.eval_outputs,
        provider_call_data=call_execution.provider_call_data,
        overall_score=call_execution.overall_score,
        tool_outputs=call_execution.tool_outputs,
    )


def _create_rerun_call_execution(call_execution):
    """Create new CreateCallExecution for rerun"""

    # Get the original call data from metadata
    call_metadata = call_execution.call_metadata or {}
    phone_number = call_execution.phone_number
    system_prompt = call_metadata.get("dynamic_prompt") or call_metadata.get(
        "base_prompt", ""
    )
    voice_settings = call_metadata.get("voice_settings", {})

    metadata = {
        "run_test_id": str(call_execution.test_execution.run_test.id),
        "scenario_id": str(call_execution.scenario.id),
        "scenario_name": call_execution.scenario.name,
        "user_id": "system",  # Use system as default since RunTest doesn't have created_by
        "agent_definition_id": str(call_execution.test_execution.agent_definition.id),
        "organization_id": str(call_execution.test_execution.run_test.organization.id),
        "row_id": str(call_execution.row_id) if call_execution.row_id else None,
        "row_data": call_metadata.get("row_data", {}),
        "dataset_id": call_metadata.get("dataset_id"),
        "base_prompt": call_metadata.get("base_prompt"),
        "dynamic_prompt": call_metadata.get("dynamic_prompt"),
        "call_direction": call_metadata.get("call_direction", "inbound"),
        "user_api_key": call_metadata.get("user_api_key"),
        "user_assistant_id": call_metadata.get("user_assistant_id"),
        "user_phone_number": call_metadata.get("user_phone_number"),
    }

    # phone_number_id is only used by VAPI; LiveKit's prepare_call
    # ignores CreateCallExecution.phone_number_id entirely.
    system_provider = os.getenv("SYSTEM_VOICE_PROVIDER", "livekit")
    if system_provider == "livekit":
        phone_number_id = ""
    elif phone_number and phone_number.startswith("+91"):
        phone_number_id = VAPI_INDIAN_PHONE_NUMBER_ID or os.getenv(
            "VAPI_PHONE_NUMBER_ID"
        )
    else:
        phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")

    metadata["call_direction"] = call_metadata.get("call_direction", "inbound")

    CreateCallExecution.objects.create(
        call_execution=call_execution,
        phone_number_id=phone_number_id,
        to_number=phone_number,
        system_prompt=system_prompt,
        metadata=metadata,
        voice_settings=voice_settings,
    )


def _rerun_call_executions(call_executions, rerun_type):
    """
    Process rerun for a list of call executions.
    Returns (successful_reruns, failed_reruns, has_pending_calls, has_pending_evals)
    """
    successful_reruns = []
    failed_reruns = []
    has_pending_calls = False
    has_pending_evals = False

    for call_execution in call_executions:
        try:
            if rerun_type == "eval_only":
                _save_eval_snapshot(call_execution)

                call_execution.eval_outputs = {}
                call_execution.call_metadata = call_execution.call_metadata or {}
                call_execution.call_metadata["eval_started"] = False
                call_execution.call_metadata["eval_completed"] = False
                call_execution.save()

                _run_simulate_evaluations_task.apply_async(
                    args=(str(call_execution.id),)
                )

                has_pending_evals = True
                logger.info(
                    f"Rerunning evaluations for call execution {call_execution.id}"
                )

            else:  # call_and_eval
                _clear_call_execution_data(call_execution)
                _create_rerun_call_execution(call_execution)

                has_pending_calls = True
                logger.info(
                    f"Rerunning call and evaluations for call execution {call_execution.id}"
                )

            successful_reruns.append(str(call_execution.id))

        except Exception as e:
            logger.error(
                f"Error rerunning call execution {call_execution.id}: {str(e)}"
            )
            failed_reruns.append(
                {"call_execution_id": str(call_execution.id), "error": str(e)}
            )

    return successful_reruns, failed_reruns, has_pending_calls, has_pending_evals


class CallExecutionRerunView(APIView):
    """
    API View to handle bulk call execution rerun requests
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gm = GeneralMethods()

    def post(self, request, test_execution_id):
        """
        Rerun multiple call executions (either evaluation only or call + evaluation)

        Args:
            test_execution_id: UUID of the TestExecution containing the calls to rerun
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the test execution
            test_execution = get_object_or_404(
                TestExecution,
                id=test_execution_id,
                run_test__organization=user_organization,
            )

            # Reject rerun if test execution is in a non-terminal status
            non_rerunnable_statuses = [
                TestExecution.ExecutionStatus.PENDING,
                TestExecution.ExecutionStatus.CANCELLING,
            ]
            if test_execution.status in non_rerunnable_statuses:
                return self._gm.bad_request(
                    f"Cannot rerun calls while test execution is in '{test_execution.status}' status. "
                    "Wait for it to complete or cancel it first."
                )

            # Validate request data
            serializer = CallExecutionRerunSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request("Invalid request data")

            rerun_type = serializer.validated_data["rerun_type"]
            select_all = serializer.validated_data.get("select_all", False)
            call_execution_ids = serializer.validated_data.get("call_execution_ids", [])

            # Validate CHAT/TEXT agents can only use eval_only rerun type
            if rerun_type != "eval_only" and test_execution.run_test.agent_definition:
                agent_type = test_execution.run_test.agent_definition.agent_type
                if agent_type == AgentDefinition.AgentTypeChoices.TEXT:
                    return self._gm.bad_request(
                        "Text/Chat agents only support 'eval_only' rerun type."
                    )
                if agent_type == AgentDefinition.AgentTypeChoices.VOICE:
                    forbidden = _voice_sim_gate_response(
                        user_organization, self._gm
                    )
                    if forbidden is not None:
                        return forbidden

            # Get call executions to rerun
            if select_all:
                # Get all call executions for this test execution that can be rerun
                call_executions = CallExecution.objects.filter(
                    test_execution=test_execution
                )
                # If call_execution_ids are provided with select_all, exclude those IDs
                if call_execution_ids:
                    call_executions = call_executions.exclude(id__in=call_execution_ids)
            else:
                # Get specific call executions
                call_executions = CallExecution.objects.filter(
                    id__in=call_execution_ids, test_execution=test_execution
                )

            if not call_executions.exists():
                return self._gm.bad_request(
                    "No call executions found that can be rerun."
                )

            # Process each call execution
            successful_reruns = []
            failed_reruns = []
            has_pending_calls = False
            has_pending_evals = False

            for call_execution in call_executions:
                try:
                    if rerun_type == "eval_only":
                        # Save eval-only snapshot before clearing
                        self._save_eval_snapshot(call_execution)

                        # Rerun evaluations only - clear eval data and reset status
                        call_execution.eval_outputs = {}

                        call_execution.call_metadata = (
                            call_execution.call_metadata or {}
                        )
                        call_execution.call_metadata["eval_started"] = False
                        call_execution.call_metadata["eval_completed"] = False
                        call_execution.save()

                        # Evals will be handled by RerunCoordinatorWorkflow below
                        has_pending_evals = True
                        logger.info(
                            f"Rerunning evaluations for call execution {call_execution.id}"
                        )

                    else:  # call_and_eval
                        # Rerun call + evaluations - clear call data
                        self._clear_call_execution_data(call_execution)

                        # Create new CreateCallExecution record for the Temporal workflow.
                        # prepare_call activity reads this to get system_prompt,
                        # voice_settings, phone number, etc.
                        _create_rerun_call_execution(call_execution)

                        # Mark as pending - will be launched via Temporal below
                        call_execution.status = CallExecution.CallStatus.PENDING
                        call_execution.save()

                        has_pending_calls = True
                        logger.info(
                            f"Rerunning call and evaluations for call execution {call_execution.id}"
                        )

                    successful_reruns.append(str(call_execution.id))

                except Exception as e:
                    logger.error(
                        f"Error rerunning call execution {call_execution.id}: {str(e)}"
                    )
                    failed_reruns.append(
                        {"call_execution_id": str(call_execution.id), "error": str(e)}
                    )

            # Update test execution status based on what we're rerunning
            if successful_reruns:
                from simulate.temporal.client import rerun_call_executions

                # Get active rerun workflow ID for merge strategy (works for any mode)
                active_workflow_id = None
                if test_execution.execution_metadata:
                    active_workflow_id = test_execution.execution_metadata.get(
                        "active_rerun_workflow_id"
                    )

                # Handle nullable workspace_id (workspace is optional on RunTest)
                workspace_id = test_execution.run_test.workspace_id
                workspace_id_str = str(workspace_id) if workspace_id else ""

                if rerun_type == "eval_only" and has_pending_evals:
                    # Launch or merge into RerunCoordinatorWorkflow for eval-only reruns
                    rerun_result = rerun_call_executions(
                        test_execution_id=str(test_execution.id),
                        call_execution_ids=successful_reruns,
                        org_id=str(user_organization.id),
                        workspace_id=workspace_id_str,
                        eval_only=True,
                        active_workflow_id=active_workflow_id,
                    )

                    # Update status to EVALUATING since Temporal is executing evals
                    test_execution.status = TestExecution.ExecutionStatus.EVALUATING
                    test_execution.picked_up_by_executor = True

                    # Store workflow info in execution_metadata
                    if not test_execution.execution_metadata:
                        test_execution.execution_metadata = {}

                    # Store active workflow ID for merge strategy (only if new workflow started)
                    if not rerun_result.get("merged"):
                        test_execution.execution_metadata[
                            "active_rerun_workflow_id"
                        ] = rerun_result.get("workflow_id")

                    test_execution.save()

                    merged_info = " (merged)" if rerun_result.get("merged") else ""
                    logger.info(
                        f"Launched RerunCoordinatorWorkflow (eval_only){merged_info} {rerun_result.get('workflow_id')} "
                        f"for test execution {test_execution.id}"
                    )

                elif rerun_type == "call_and_eval" and has_pending_calls:
                    # Launch or merge into RerunCoordinatorWorkflow for call reruns
                    rerun_result = rerun_call_executions(
                        test_execution_id=str(test_execution.id),
                        call_execution_ids=successful_reruns,
                        org_id=str(user_organization.id),
                        workspace_id=workspace_id_str,
                        eval_only=False,
                        active_workflow_id=active_workflow_id,
                    )

                    # Update status to RUNNING since Temporal is executing
                    test_execution.status = TestExecution.ExecutionStatus.RUNNING
                    test_execution.picked_up_by_executor = True

                    # Store workflow info in execution_metadata
                    if not test_execution.execution_metadata:
                        test_execution.execution_metadata = {}

                    # Store active workflow ID for merge strategy (only if new workflow started)
                    if not rerun_result.get("merged"):
                        test_execution.execution_metadata[
                            "active_rerun_workflow_id"
                        ] = rerun_result.get("workflow_id")

                    test_execution.save()

                    merged_info = " (merged)" if rerun_result.get("merged") else ""
                    logger.info(
                        f"Launched RerunCoordinatorWorkflow{merged_info} {rerun_result.get('workflow_id')} "
                        f"for test execution {test_execution.id}"
                    )

            return Response(
                {
                    "message": f"Bulk call execution rerun initiated successfully ({rerun_type})",
                    "test_execution_id": str(test_execution_id),
                    "rerun_type": rerun_type,
                    "total_processed": len(successful_reruns) + len(failed_reruns),
                    "successful_reruns": successful_reruns,
                    "failed_reruns": failed_reruns,
                    "success_count": len(successful_reruns),
                    "failure_count": len(failed_reruns),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error in bulk call execution rerun: {str(e)}")
            traceback.print_exc()
            return Response(
                {"error": "Failed to rerun call executions"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _clear_call_execution_data(self, call_execution: CallExecution):
        """Clear all call execution data for rerun"""

        # Create snapshot of current state before clearing
        CallExecutionSnapshot.objects.create(
            call_execution=call_execution,
            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
            service_provider_call_id=call_execution.service_provider_call_id,
            status=call_execution.status,
            started_at=call_execution.started_at,
            completed_at=call_execution.completed_at,
            ended_at=call_execution.ended_at,
            duration_seconds=call_execution.duration_seconds,
            recording_url=call_execution.recording_url,
            cost_cents=call_execution.cost_cents,
            stt_cost_cents=call_execution.stt_cost_cents,
            llm_cost_cents=call_execution.llm_cost_cents,
            tts_cost_cents=call_execution.tts_cost_cents,
            vapi_cost_cents=call_execution.vapi_cost_cents,
            call_summary=call_execution.call_summary,
            ended_reason=call_execution.ended_reason,
            overall_score=call_execution.overall_score,
            response_time_ms=call_execution.response_time_ms,
            assistant_id=call_execution.assistant_id,
            customer_number=call_execution.customer_number,
            call_type=call_execution.call_type,
            analysis_data=call_execution.analysis_data,
            evaluation_data=call_execution.evaluation_data,
            message_count=call_execution.message_count,
            transcript_available=call_execution.transcript_available,
            recording_available=call_execution.recording_available,
            eval_outputs=call_execution.eval_outputs,
            tool_outputs=call_execution.tool_outputs,
            provider_call_data=call_execution.provider_call_data,
            avg_agent_latency_ms=call_execution.avg_agent_latency_ms,
            user_interruption_count=call_execution.user_interruption_count,
            user_interruption_rate=call_execution.user_interruption_rate,
            user_wpm=call_execution.user_wpm,
            bot_wpm=call_execution.bot_wpm,
            talk_ratio=call_execution.talk_ratio,
            ai_interruption_count=call_execution.ai_interruption_count,
            ai_interruption_rate=call_execution.ai_interruption_rate,
            avg_stop_time_after_interruption_ms=call_execution.avg_stop_time_after_interruption_ms,
            conversation_metrics_data=call_execution.conversation_metrics_data,
            transcripts=list(
                call_execution.transcripts.values(
                    "speaker_role",
                    "content",
                    "start_time_ms",
                    "end_time_ms",
                    "confidence_score",
                )
            ),
        )

        # Clear existing transcripts
        call_execution.transcripts.all().delete()
        call_execution.reset_to_default()
        call_execution.save()

    def _save_eval_snapshot(self, call_execution):
        """Save a snapshot of evaluation data only (for eval_only reruns)"""
        from simulate.models.test_execution import CallExecutionSnapshot

        # Create snapshot with only evaluation data
        CallExecutionSnapshot.objects.create(
            call_execution=call_execution,
            rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
            eval_outputs=call_execution.eval_outputs,
            provider_call_data=call_execution.provider_call_data,
            overall_score=call_execution.overall_score,
            tool_outputs=call_execution.tool_outputs,
        )


class TestExecutionRerunView(APIView):
    """
    API View to handle bulk test execution rerun requests.
    Reruns all call executions for each specified test execution.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gm = GeneralMethods()

    def _prepare_call_executions_for_rerun_bulk(
        self, call_executions, rerun_type: str
    ) -> tuple[list[str], list[dict]]:
        """
        Prepare call executions for rerun using bulk operations.

        This is an optimized version that uses bulk_create and bulk_update
        instead of individual save() calls. For large datasets, this reduces
        database round trips from O(2N) to O(4) regardless of N.

        Args:
            call_executions: QuerySet of CallExecution objects to prepare
            rerun_type: Either "eval_only" or "call_and_eval"

        Returns:
            tuple: (successful_rerun_ids: list[str], failed_reruns: list[dict])
        """
        from simulate.models.test_execution import CallTranscript

        BATCH_SIZE = 500
        successful_reruns = []
        failed_reruns = []
        snapshots_to_create = []
        call_executions_to_update = []
        call_ids_to_delete_transcripts = []

        # First pass: prepare all objects in memory
        for call_execution in call_executions:
            try:
                if rerun_type == "eval_only":
                    # Prepare eval-only snapshot
                    snapshots_to_create.append(
                        CallExecutionSnapshot(
                            call_execution=call_execution,
                            rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
                            eval_outputs=call_execution.eval_outputs,
                            provider_call_data=call_execution.provider_call_data,
                            overall_score=call_execution.overall_score,
                            tool_outputs=call_execution.tool_outputs,
                        )
                    )
                    # Prepare call execution updates
                    call_execution.eval_outputs = {}
                    call_execution.call_metadata = call_execution.call_metadata or {}
                    call_execution.call_metadata["eval_started"] = False
                    call_execution.call_metadata["eval_completed"] = False
                    call_executions_to_update.append(call_execution)
                else:
                    # call_and_eval: Capture transcript data before we delete them
                    transcript_data = list(
                        call_execution.transcripts.values(
                            "speaker_role",
                            "content",
                            "start_time_ms",
                            "end_time_ms",
                            "confidence_score",
                        )
                    )

                    # Prepare full snapshot with all fields
                    snapshots_to_create.append(
                        CallExecutionSnapshot(
                            call_execution=call_execution,
                            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
                            service_provider_call_id=call_execution.service_provider_call_id,
                            status=call_execution.status,
                            started_at=call_execution.started_at,
                            completed_at=call_execution.completed_at,
                            ended_at=call_execution.ended_at,
                            duration_seconds=call_execution.duration_seconds,
                            recording_url=call_execution.recording_url,
                            stereo_recording_url=call_execution.stereo_recording_url,
                            cost_cents=call_execution.cost_cents,
                            stt_cost_cents=call_execution.stt_cost_cents,
                            llm_cost_cents=call_execution.llm_cost_cents,
                            tts_cost_cents=call_execution.tts_cost_cents,
                            vapi_cost_cents=call_execution.vapi_cost_cents,
                            call_summary=call_execution.call_summary,
                            ended_reason=call_execution.ended_reason,
                            overall_score=call_execution.overall_score,
                            response_time_ms=call_execution.response_time_ms,
                            assistant_id=call_execution.assistant_id,
                            customer_number=call_execution.customer_number,
                            call_type=call_execution.call_type,
                            analysis_data=call_execution.analysis_data,
                            evaluation_data=call_execution.evaluation_data,
                            message_count=call_execution.message_count,
                            transcript_available=call_execution.transcript_available,
                            recording_available=call_execution.recording_available,
                            eval_outputs=call_execution.eval_outputs,
                            tool_outputs=call_execution.tool_outputs,
                            provider_call_data=call_execution.provider_call_data,
                            monitor_call_data=call_execution.monitor_call_data,
                            avg_agent_latency_ms=call_execution.avg_agent_latency_ms,
                            user_interruption_count=call_execution.user_interruption_count,
                            user_interruption_rate=call_execution.user_interruption_rate,
                            user_wpm=call_execution.user_wpm,
                            bot_wpm=call_execution.bot_wpm,
                            talk_ratio=call_execution.talk_ratio,
                            ai_interruption_count=call_execution.ai_interruption_count,
                            ai_interruption_rate=call_execution.ai_interruption_rate,
                            avg_stop_time_after_interruption_ms=call_execution.avg_stop_time_after_interruption_ms,
                            conversation_metrics_data=call_execution.conversation_metrics_data,
                            transcripts=transcript_data,
                        )
                    )

                    # Mark for transcript deletion
                    call_ids_to_delete_transcripts.append(call_execution.id)

                    # Reset all fields using the model method (without save)
                    call_execution.reset_to_default(save=False)
                    call_executions_to_update.append(call_execution)

                    # Create CreateCallExecution record for the Temporal workflow
                    _create_rerun_call_execution(call_execution)

                successful_reruns.append(str(call_execution.id))

            except Exception as e:
                logger.error(
                    f"Error preparing call execution {call_execution.id} for rerun: {str(e)}"
                )
                failed_reruns.append(
                    {"call_execution_id": str(call_execution.id), "error": str(e)}
                )

        # Second pass: bulk database operations in a single transaction
        if snapshots_to_create or call_executions_to_update:
            with transaction.atomic():
                # Bulk delete transcripts for call_and_eval mode
                if call_ids_to_delete_transcripts:
                    CallTranscript.objects.filter(
                        call_execution_id__in=call_ids_to_delete_transcripts
                    ).delete()

                # Bulk create all snapshots
                if snapshots_to_create:
                    CallExecutionSnapshot.objects.bulk_create(
                        snapshots_to_create, batch_size=BATCH_SIZE
                    )

                # Bulk update all call executions
                if call_executions_to_update:
                    if rerun_type == "eval_only":
                        CallExecution.objects.bulk_update(
                            call_executions_to_update,
                            ["eval_outputs", "call_metadata"],
                            batch_size=BATCH_SIZE,
                        )
                    else:
                        CallExecution.objects.bulk_update(
                            call_executions_to_update,
                            CallExecution.RESET_FIELDS,
                            batch_size=BATCH_SIZE,
                        )

        return successful_reruns, failed_reruns

    def post(self, request, run_test_id):
        """
        Rerun multiple test executions (either evaluation only or call + evaluation).
        All call executions within each test execution are rerun.

        Args:
            run_test_id: UUID of the RunTest containing the test executions to rerun
        """
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                organization=user_organization,
            )

            serializer = TestExecutionRerunSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            rerun_type = serializer.validated_data["rerun_type"]
            select_all = serializer.validated_data.get("select_all", False)
            test_execution_ids = serializer.validated_data.get("test_execution_ids", [])

            # Validate CHAT/TEXT agents can only use eval_only rerun type
            if rerun_type != "eval_only" and run_test.agent_definition:
                agent_type = run_test.agent_definition.agent_type
                if agent_type == AgentDefinition.AgentTypeChoices.TEXT:
                    return self._gm.bad_request(
                        "Text/Chat agents only support 'eval_only' rerun type."
                    )
                if agent_type == AgentDefinition.AgentTypeChoices.VOICE:
                    forbidden = _voice_sim_gate_response(
                        user_organization, self._gm
                    )
                    if forbidden is not None:
                        return forbidden

            # Get test executions to rerun, excluding those in non-terminal statuses
            non_rerunnable_statuses = [
                TestExecution.ExecutionStatus.PENDING,
                TestExecution.ExecutionStatus.RUNNING,
                TestExecution.ExecutionStatus.CANCELLING,
            ]
            if select_all:
                test_executions = TestExecution.objects.filter(
                    run_test=run_test
                ).exclude(status__in=non_rerunnable_statuses)
                if test_execution_ids:
                    test_executions = test_executions.exclude(id__in=test_execution_ids)
            else:
                test_executions = TestExecution.objects.filter(
                    id__in=test_execution_ids, run_test=run_test
                ).exclude(status__in=non_rerunnable_statuses)

            if not test_executions.exists():
                return self._gm.bad_request(
                    "No test executions found that can be rerun. "
                    "Executions in pending, running, or cancelling status cannot be rerun."
                )

            from simulate.temporal.client import rerun_call_executions

            results = []
            overall_success_count = 0
            overall_failure_count = 0

            for test_execution in test_executions:
                call_executions = CallExecution.objects.filter(
                    test_execution=test_execution
                )

                if not call_executions.exists():
                    results.append(
                        {
                            "test_execution_id": str(test_execution.id),
                            "success_count": 0,
                            "failure_count": 0,
                            "successful_reruns": [],
                            "failed_reruns": [],
                            "skipped": True,
                            "reason": "No call executions found",
                        }
                    )
                    continue

                # Prepare call executions (DB snapshot + reset) using bulk operations
                successful_reruns, failed_reruns = (
                    self._prepare_call_executions_for_rerun_bulk(
                        call_executions, rerun_type
                    )
                )

                # Start Temporal RerunCoordinatorWorkflow for this test execution
                if successful_reruns:
                    active_workflow_id = None
                    if test_execution.execution_metadata:
                        active_workflow_id = test_execution.execution_metadata.get(
                            "active_rerun_workflow_id"
                        )

                    workspace_id = test_execution.run_test.workspace_id
                    workspace_id_str = str(workspace_id) if workspace_id else ""
                    eval_only = rerun_type == "eval_only"

                    rerun_result = rerun_call_executions(
                        test_execution_id=str(test_execution.id),
                        call_execution_ids=successful_reruns,
                        org_id=str(user_organization.id),
                        workspace_id=workspace_id_str,
                        eval_only=eval_only,
                        active_workflow_id=active_workflow_id,
                    )

                    # Update test execution status
                    if eval_only:
                        test_execution.status = TestExecution.ExecutionStatus.EVALUATING
                    else:
                        test_execution.status = TestExecution.ExecutionStatus.RUNNING

                    test_execution.picked_up_by_executor = True

                    if not test_execution.execution_metadata:
                        test_execution.execution_metadata = {}
                    if not rerun_result.get("merged"):
                        test_execution.execution_metadata[
                            "active_rerun_workflow_id"
                        ] = rerun_result.get("workflow_id")

                    test_execution.save()

                    merged_info = " (merged)" if rerun_result.get("merged") else ""
                    logger.info(
                        f"Launched RerunCoordinatorWorkflow{merged_info} "
                        f"{rerun_result.get('workflow_id')} for test execution "
                        f"{test_execution.id} ({len(successful_reruns)} calls)"
                    )

                overall_success_count += len(successful_reruns)
                overall_failure_count += len(failed_reruns)

                results.append(
                    {
                        "test_execution_id": str(test_execution.id),
                        "success_count": len(successful_reruns),
                        "failure_count": len(failed_reruns),
                        "successful_reruns": successful_reruns,
                        "failed_reruns": failed_reruns,
                    }
                )

            return Response(
                {
                    "message": f"Bulk test execution rerun initiated successfully ({rerun_type})",
                    "run_test_id": str(run_test_id),
                    "rerun_type": rerun_type,
                    "total_test_executions": len(results),
                    "results": results,
                    "overall_success_count": overall_success_count,
                    "overall_failure_count": overall_failure_count,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error in bulk test execution rerun: {str(e)}")
            traceback.print_exc()
            return Response(
                {"error": "Failed to rerun test executions"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RunNewEvalsOnTestExecutionView(APIView):
    """
    API View to run new evaluations on existing test executions
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gm = GeneralMethods()

    def post(self, request, run_test_id):
        """
        Run new evaluations on multiple test executions

        Args:
            run_test_id: UUID of the RunTest containing the test executions

        Request Body:
            test_execution_ids: List of test execution IDs (optional if select_all=True)
            select_all: Boolean to run on all test executions (default: False)
            eval_config_ids: List of SimulateEvalConfig IDs to run (required)
            enable_tool_evaluation: Boolean to enable/disable tool evaluation (optional)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            # Validate request data
            serializer = RunNewEvalsOnTestExecutionSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            select_all = serializer.validated_data.get("select_all", False)
            test_execution_ids = serializer.validated_data.get("test_execution_ids", [])
            eval_config_ids = serializer.validated_data.get("eval_config_ids", [])
            enable_tool_evaluation = serializer.validated_data.get(
                "enable_tool_evaluation"
            )

            # Update run_test.enable_tool_evaluation if provided
            if enable_tool_evaluation is not None:
                run_test.enable_tool_evaluation = enable_tool_evaluation
                run_test.save(update_fields=["enable_tool_evaluation"])
                logger.info(
                    f"Updated enable_tool_evaluation to {enable_tool_evaluation} for run test {run_test.id}"
                )

            # Get test executions to run evaluations on
            if select_all:
                # Get all test executions for this run test
                test_executions = TestExecution.objects.filter(run_test=run_test)
                # If test_execution_ids are provided with select_all, exclude those IDs
                if test_execution_ids:
                    test_executions = test_executions.exclude(id__in=test_execution_ids)
            else:
                # Get specific test executions
                test_executions = TestExecution.objects.filter(
                    id__in=test_execution_ids, run_test=run_test
                )

            if not test_executions.exists():
                return self._gm.bad_request(
                    "No test executions found to run evaluations on."
                )

            # Validate that all test executions have COMPLETED status
            non_completed_executions = test_executions.exclude(
                status=TestExecution.ExecutionStatus.COMPLETED
            )

            if non_completed_executions.exists():
                return self._gm.bad_request(
                    "Only test executions with COMPLETED status can have new evaluations run on them."
                )

            # Validate eval configs exist and belong to the same run test
            eval_configs = SimulateEvalConfig.objects.filter(
                id__in=eval_config_ids, run_test=run_test
            )

            if eval_configs.count() != len(eval_config_ids):
                return self._gm.bad_request(
                    "One or more eval configs not found or do not belong to this run test."
                )

            # Collect all call execution IDs from the selected test executions
            # Also update test execution status and column order.
            #
            # Memory-bounded rewrite: stream test executions with
            # ``.iterator(chunk_size=100)`` so the queryset is not fully
            # materialized, fetch call-execution ids in one bulk query
            # (joined via ``test_execution_id__in``) instead of per-row,
            # and flush ``bulk_update`` in batches of 100 so the buffer
            # itself stays bounded for large runs.
            BATCH_SIZE = 100
            call_execution_ids = []
            test_execution_count = 0
            updated_test_executions = []
            test_executions_to_update = []

            # Precompute the eval-config columns once; they are identical
            # for every test_execution so recomputing inside the loop is
            # pure overhead.
            eval_column_entries = [
                {
                    "column_name": eval_config.name,
                    "id": str(eval_config.id),
                    "eval_config": eval_config.eval_template.config,
                    "visible": True,
                    "type": "evaluation",
                }
                for eval_config in eval_configs
            ]

            def _flush_bulk_update(buffer):
                if buffer:
                    TestExecution.objects.bulk_update(
                        buffer,
                        [
                            "status",
                            "execution_metadata",
                            "picked_up_by_executor",
                        ],
                    )
                    buffer.clear()

            # Bulk-fetch all matching CallExecution ids in a single query
            # (subquery against test_executions) rather than one query per
            # test execution (N+1). Using ``.values_list("id")`` as a
            # subquery keeps the id materialization server-side.
            ce_rows = CallExecution.objects.filter(
                test_execution_id__in=test_executions.values_list("id", flat=True)
            ).values_list("id", flat=True)
            for ce_id in ce_rows.iterator(chunk_size=1000):
                call_execution_ids.append(str(ce_id))

            for test_execution in test_executions.iterator(chunk_size=BATCH_SIZE):
                test_execution_count += 1

                # Update test execution status to EVALUATING
                test_execution.status = TestExecution.ExecutionStatus.EVALUATING

                # Update column_order to include new eval configs
                if not test_execution.execution_metadata:
                    test_execution.execution_metadata = {}

                column_order = test_execution.execution_metadata.get("column_order", [])
                if not column_order:
                    column_order = []

                # Normalize legacy camelCase keys in stored column_order entries.
                for _col in column_order:
                    if (
                        isinstance(_col, dict)
                        and "columnName" in _col
                        and "column_name" not in _col
                    ):
                        _col["column_name"] = _col.pop("columnName")

                # Get existing eval config IDs in column order
                existing_eval_ids = set()
                for col in column_order:
                    if col.get("type") == "evaluation":
                        existing_eval_ids.add(col.get("id"))

                # Add new eval configs to column order if they don't exist
                for entry in eval_column_entries:
                    if entry["id"] not in existing_eval_ids:
                        column_order.append(dict(entry))
                        logger.info(
                            f"Added eval config {entry['column_name']} to column order "
                            f"for test execution {test_execution.id}"
                        )

                test_execution.execution_metadata["column_order"] = column_order
                test_execution.picked_up_by_executor = False
                test_executions_to_update.append(test_execution)
                updated_test_executions.append(str(test_execution.id))

                if len(test_executions_to_update) >= BATCH_SIZE:
                    _flush_bulk_update(test_executions_to_update)

            # Flush remainder
            _flush_bulk_update(test_executions_to_update)

            if not call_execution_ids:
                return self._gm.bad_request(
                    "No call executions found in the selected test executions."
                )

            # Convert eval_config_ids to strings
            eval_config_ids_str = [str(ec_id) for ec_id in eval_config_ids]

            # Bulk update eval_started flag and initialize eval_outputs for all call executions before triggering tasks
            call_executions_to_update = CallExecution.objects.filter(
                id__in=call_execution_ids
            )
            call_executions_list = []
            for call_execution in call_executions_to_update:
                # Provider-agnostic eval flags live in call_metadata
                call_execution.call_metadata = call_execution.call_metadata or {}
                call_execution.call_metadata["eval_started"] = True
                call_execution.call_metadata["eval_completed"] = False

                # Initialize eval_outputs for the new eval configs
                if not call_execution.eval_outputs:
                    call_execution.eval_outputs = {}

                # Set placeholder values for each eval config that will be run
                for eval_config in eval_configs:
                    call_execution.eval_outputs[str(eval_config.id)] = {
                        "status": "pending"
                    }

                call_executions_list.append(call_execution)

            if call_executions_list:
                CallExecution.objects.bulk_update(
                    call_executions_list, ["call_metadata", "eval_outputs"]
                )
                logger.info(
                    f"Bulk updated eval_started flag and initialized eval_outputs for "
                    f"{len(call_executions_list)} call executions with {len(eval_configs)} eval configs"
                )

            # Trigger the Celery task to run evaluations asynchronously
            task = run_new_evals_on_call_executions_task.apply_async(
                args=(call_execution_ids, eval_config_ids_str),
            )
            task_id = task.id

            logger.info(
                f"Triggered new evaluations task {task_id} for {len(call_execution_ids)} call executions "
                f"across {test_execution_count} test executions with {len(eval_config_ids)} eval configs. "
                f"Updated {len(updated_test_executions)} test executions to EVALUATING status. "
                f"Individual tasks will be spawned for parallel processing."
            )

            return Response(
                {
                    "message": "New evaluations dispatched successfully. Individual tasks will run in parallel.",
                    "run_test_id": str(run_test_id),
                    "task_id": task_id,
                    "test_execution_count": test_execution_count,
                    "updated_test_executions": updated_test_executions,
                    "call_execution_count": len(call_execution_ids),
                    "eval_config_count": len(eval_config_ids),
                    "total_evaluations_to_run": len(call_execution_ids)
                    * len(eval_config_ids),
                    "note": f"{len(call_execution_ids)} parallel tasks will be spawned to process evaluations",
                    "status_update": f"Set {len(updated_test_executions)} test executions to EVALUATING status",
                    "column_order_updated": "New eval configs added to column order for UI visibility",
                    "eval_configs": [
                        {"id": str(ec.id), "name": ec.name} for ec in eval_configs
                    ],
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error running new evaluations on test executions: {str(e)}")
            traceback.print_exc()
            return Response(
                {
                    "error": "Failed to run new evaluations on test executions",
                    "details": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


def add_trace_details_to_call_executions(call_executions):
    """Add trace details to call executions"""

    call_executions_dict = {}
    call_execution_ids = []

    for call_execution in call_executions:
        if call_execution.get("id", None):
            call_execution_ids.append(str(call_execution.get("id")))
            call_executions_dict[str(call_execution.get("id"))] = call_execution

    # ObservationSpan.eval_attributes is a *flat* JSON dict. The call execution id is stored under a dotted key:
    # "fi.simulator.call_execution_id" (snake_case; FE sees camelCase due to middleware).
    #
    # We bulk-fetch spans for all call_execution_ids on the page to avoid N+1 queries.

    # Build Q objects for filtering by multiple call execution IDs
    q_objects = Q()
    for call_exec_id in call_execution_ids:
        q_objects |= Q(
            eval_attributes__contains={"fi.simulator.call_execution_id": call_exec_id}
        )

    matching_spans = (
        ObservationSpan.objects.filter(
            deleted=False,
        )
        .filter(q_objects)
        .select_related("trace")
        .only("id", "trace_id", "eval_attributes")
    )

    # Collect trace IDs and build trace_details
    trace_ids = set()
    trace_details_map = {}

    for span in matching_spans:
        # Extract call_execution_id from eval_attributes
        call_exec_id = (
            span.eval_attributes.get("fi.simulator.call_execution_id")
            if span.eval_attributes
            else None
        )
        if call_exec_id and str(call_exec_id) in call_executions_dict:
            trace_id = span.trace.id
            trace_ids.add(trace_id)
            # Use the first span found for each call_execution_id
            if str(call_exec_id) not in trace_details_map:
                trace_details_map[str(call_exec_id)] = {
                    "trace_id": str(trace_id),
                    "parent_span_id": str(span.id),
                    "attributes": span.eval_attributes,
                    "_trace_id_uuid": trace_id,  # Keep UUID for mapping
                }

    # Bulk fetch session IDs for all traces
    if trace_ids:
        trace_to_session = {}
        traces_with_sessions = Trace.objects.filter(
            id__in=trace_ids,
            session__isnull=False,
        ).values_list("id", "session_id")

        for trace_id, session_id in traces_with_sessions:
            trace_to_session[trace_id] = str(session_id)

        # Add sessions to trace_details (as list for consistency with serializer format)
        for call_exec_id, trace_details in trace_details_map.items():
            trace_id_uuid = trace_details["_trace_id_uuid"]
            if trace_id_uuid in trace_to_session:
                trace_details["simulated_sessions"] = [trace_to_session[trace_id_uuid]]
            else:
                trace_details["simulated_sessions"] = []
            # Remove temporary UUID key
            del trace_details["_trace_id_uuid"]
    else:
        # No trace_ids found, ensure all trace_details have sessions key
        for trace_details in trace_details_map.values():
            trace_details["simulated_sessions"] = []
            del trace_details["_trace_id_uuid"]

    # Add trace_details to call_executions_dict
    for call_exec_id, trace_details in trace_details_map.items():
        call_executions_dict[call_exec_id]["trace_details"] = trace_details

    # Add dataset session_id (from Row.metadata) to grouped/flattened call executions.
    # We only do one Row query for the whole page to avoid N+1.
    row_ids = set()
    for call_execution in call_executions_dict.values():
        call_metadata = call_execution.get("call_metadata") or {}
        row_id = call_execution.get("row_id") or call_metadata.get("row_id")
        if row_id:
            row_ids.add(str(row_id))

    row_session_id_map = {}
    if row_ids:
        for row_id, metadata in Row.all_objects.filter(id__in=row_ids).values_list(
            "id", "metadata"
        ):
            if isinstance(metadata, dict) and "session_id" in metadata:
                row_session_id_map[str(row_id)] = metadata.get("session_id")

    for call_execution in call_executions_dict.values():
        call_metadata = call_execution.get("call_metadata") or {}
        row_id = call_execution.get("row_id") or call_metadata.get("row_id")
        call_execution["session_id"] = (
            row_session_id_map.get(str(row_id)) if row_id else None
        )

    return list(call_executions_dict.values())
