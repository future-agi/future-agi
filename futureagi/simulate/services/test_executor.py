import gc
import json
import os
import re
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import chain
from typing import Any, Dict, Optional
from uuid import uuid4

import structlog

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.tool_eval_agent.tool_eval_agent import ToolEvalAgent
except ImportError:
    ToolEvalAgent = _ee_stub("ToolEvalAgent")
from django.core.exceptions import ObjectDoesNotExist
from django.db import close_old_connections, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from simulate.models.call_log_entry import CallLogEntry

# from simulate.services.call_monitor import CallMonitor
try:
    from ee.voice.semantics import FAGICallData
    from ee.voice.services.voice_service_manager import VoiceServiceManager
except ImportError:
    FAGICallData = None
    VoiceServiceManager = None
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)

try:
    from ee.evals.futureagi.eval_deterministic.evaluator import DeterministicEvaluator
except ImportError:
    DeterministicEvaluator = _ee_stub("DeterministicEvaluator")

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.tasks.user_evaluation import trigger_error_localization_for_simulate
from model_hub.views.utils.evals import run_eval_func
from sdk.utils.helpers import _get_api_call_type
from simulate.constants.persona_prompt_guides import (
    CHAT_COMMUNICATION_STYLE_GUIDES,
    CHAT_EMOJI_FREQUENCY_GUIDES,
    CHAT_PERSONALITY_GUIDES,
    CHAT_PUNCTUATION_STYLE_GUIDES,
    CHAT_REGIONAL_MIX_GUIDES,
    CHAT_SLANG_LEVEL_GUIDES,
    CHAT_TONE_GUIDES,
    CHAT_TYPO_LEVEL_GUIDES,
    CHAT_VERBOSITY_GUIDES,
    VOICE_COMMUNICATION_STYLE_GUIDES,
    VOICE_PERSONALITY_GUIDES,
)
try:
    from ee.voice.constants.voice_mapper import (
        select_voice_id,
    )
except ImportError:
    select_voice_id = None
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    CallExecution,
    CallTranscript,
    ChatMessageModel,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.models.run_test import CreateCallExecution
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.pydantic_schemas.chat import SimulationCallType
from simulate.services.branch_deviation_analyzer import BranchDeviationAnalyzer
try:
    from ee.voice.services.conversation_metrics import ConversationMetricsCalculator
    from ee.voice.services.phone_number_service import PhoneNumberService
    from ee.voice.utils.processing_gating import decide_processing_skip
except ImportError:
    ConversationMetricsCalculator = None
    PhoneNumberService = None
    decide_processing_skip = None
from simulate.utils.processing_outcomes import (
    build_skipped_eval_output_payload,
    set_processing_skip_metadata,
)
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from tfc.settings.settings import VAPI_INDIAN_PHONE_NUMBER_ID
from tfc.temporal.drop_in import temporal_activity

# Note: run_eval_summary_task imported lazily to avoid circular imports
from tfc.utils.error_codes import get_specific_error_message
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallType
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices
    APICallType = None
try:
    from ee.usage.services.metering import check_usage
except ImportError:
    check_usage = None
try:
    from ee.usage.utils.usage_entries import deduct_cost_for_request, log_and_deduct_cost_for_api_request
except ImportError:
    deduct_cost_for_request = None
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request


class TestExecutor:
    """DEPRECATED: Legacy test executor. Do not add new tasks here.

    Use Temporal workflows/activities instead. See tfc/temporal/ for patterns.

    Original purpose: Service to execute test runs with multiple scenarios.
    Handles the orchestration of running scenarios, monitoring calls, and collecting results.
    """

    def __init__(
        self, monitor_interval: int = 30, system_voice_provider=ProviderChoices.VAPI
    ):
        """
        Initialize the test executor

        Args:
            monitor_interval: How often to check test progress (seconds)
        """
        self.monitor_interval = monitor_interval
        self.running = False
        self.monitor_thread = None
        self.voice_service_manager = (
            VoiceServiceManager(system_voice_provider=system_voice_provider)
            if VoiceServiceManager
            else None
        )
        self.system_voice_provider = system_voice_provider
        self.active_tests: dict[str, dict] = {}  # Track active test executions

    def _background_sound_enabled(self, raw_value: Any) -> bool:
        """Normalize persona background_sound flag to a strict boolean."""
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() == "true"
        return False

    def start(self):
        """Start the test executor service"""
        if self.running:
            logger.info("Test executor is already running")
            return

        self.running = True

        logger.info("Test executor started successfully")

    def monitor_test_execution(self, test_execution, poll_interval=20):
        """
        Background thread to monitor and update call executions for a test.
        """
        try:
            # Optimized query: limit fields and use only necessary select_related
            call_executions = (
                CallExecution.objects.filter(test_execution_id=test_execution.id)
                .exclude(
                    status=CallExecution.CallStatus.CANCELLED,
                    simulation_call_type=CallExecution.SimulationCallType.TEXT,
                )
                .select_related("agent_version")
                .only(
                    "id",
                    "status",
                    "service_provider_call_id",
                    "call_metadata",
                    "provider_call_data",
                    "completed_at",
                    "ended_reason",
                    "test_execution_id",
                    "agent_version__id",
                    "agent_version__configuration_snapshot",
                )
            )

            # If there are no call executions, mark as completed
            if not call_executions.exists():
                test = TestExecution.objects.filter(id=test_execution.id).get()
                if test.status != TestExecution.ExecutionStatus.CANCELLED:
                    test_execution.status = TestExecution.ExecutionStatus.COMPLETED
                    test_execution.save()
                    logger.info(
                        f"Test execution {test_execution.id} marked as completed"
                    )

                    test_execution.eval_explanation_summary_status = (
                        EvalExplanationSummaryStatus.PENDING
                    )
                    test_execution.save(
                        update_fields=["eval_explanation_summary_status"]
                    )
                    # Lazy import to avoid circular dependency
                    from simulate.tasks.eval_summary_tasks import run_eval_summary_task

                    run_eval_summary_task.apply_async(args=(str(test_execution.id),))

                    test_execution.picked_up_by_executor = False
                    test_execution.save()
                    return

            all_done = True

            for call in call_executions:
                close_old_connections()
                # Skip if call is already in a final state (including CANCELLED)
                if call.status in [
                    CallExecution.CallStatus.COMPLETED,
                    CallExecution.CallStatus.FAILED,
                    CallExecution.CallStatus.CANCELLED,
                ]:
                    continue

                if (
                    call.status not in [CallExecution.CallStatus.ANALYZING]
                    and call.service_provider_call_id
                ):
                    # Check status from service provider
                    try:
                        # Detect call direction and use appropriate credentials
                        is_outbound = (
                            call.call_metadata.get("call_direction") == "outbound"
                        )

                        if is_outbound:
                            # Use user's credentials for outbound calls
                            # Use pre-loaded test_execution to avoid N+1 queries
                            agent_def = test_execution.run_test.agent_definition
                            agent_version = (
                                call.agent_version
                                if call.agent_version
                                else agent_def.latest_version
                            )
                            snapshot = agent_version.configuration_snapshot
                            api_key = (
                                snapshot.get("api_key")
                                if snapshot and snapshot.get("api_key")
                                else None
                            )
                            if not api_key:
                                error_msg = f"Outbound call {call.id} is missing an api_key on AgentVersion {agent_version.id if agent_version else None}. Cancelling."
                                logger.error(error_msg)
                                call.status = CallExecution.CallStatus.FAILED
                                call.ended_reason = "Configuration error: Missing API key for outbound call."
                                call.completed_at = timezone.now()
                                call.save()
                                continue

                            voice_service_manager = VoiceServiceManager(api_key=api_key)
                        else:
                            # Use system credentials for inbound calls
                            voice_service_manager = self.voice_service_manager

                        normalized_call_data = voice_service_manager.get_call(
                            call.service_provider_call_id, False
                        )
                        call = CallExecution.objects.filter(id=call.id).get()
                        new_status = normalized_call_data.status

                        # Double-check that call hasn't been cancelled before updating status
                        # This prevents race conditions where cancellation happens during Vapi check
                        if call.status == CallExecution.CallStatus.CANCELLED:
                            continue

                        call.status = new_status
                        call.save(update_fields=["status"])
                        # else: still running

                    except Exception as e:
                        error_message = str(e)
                        logger.error(
                            f"Error checking call status for {call.service_provider_call_id}: {error_message}"
                        )

                        # Double-check that call hasn't been cancelled before marking as failed
                        if call.status == CallExecution.CallStatus.CANCELLED:
                            continue

                        # Mark call as failed for any error
                        try:
                            call.status = CallExecution.CallStatus.FAILED
                            call.ended_reason = f"Error: {error_message[:10000]}"
                            call.completed_at = timezone.now()
                            call.call_metadata = call.call_metadata or {}
                            call.call_metadata["error"] = error_message
                            call.call_metadata["failure_reason"] = "api_error"
                            call.save(
                                update_fields=[
                                    "status",
                                    "ended_reason",
                                    "completed_at",
                                    "call_metadata",
                                ]
                            )
                            logger.info(
                                f"Call {call.id} marked as failed due to error: {error_message}"
                            )
                        except Exception as save_error:
                            logger.error(
                                f"Error marking call as failed: {str(save_error)}"
                            )
                            traceback.print_exc()
                if call.status in [
                    CallExecution.CallStatus.ANALYZING,
                    CallExecution.CallStatus.COMPLETED,
                    CallExecution.CallStatus.FAILED,
                ]:
                    if not call.call_metadata:
                        call.call_metadata = {}

                    # Safely check call_data_stored using string provider key
                    provider_key = str(self.system_voice_provider.value)
                    pcd = (
                        call.provider_call_data
                        if isinstance(call.provider_call_data, dict)
                        else {}
                    )
                    provider_entry = (
                        pcd.get(provider_key)
                        if isinstance(pcd.get(provider_key), dict)
                        else {}
                    )
                    call_data_stored = provider_entry.get("call_data_stored")

                    if not call_data_stored:
                        self._store_complete_call_data(str(call.id))
                        # Refresh to get updated call_metadata/provider_call_data from _store_complete_call_data
                        call.refresh_from_db()
                        if call.status == CallExecution.CallStatus.COMPLETED:
                            # Check if evaluations have been completed for this call
                            if not call.call_metadata.get("eval_started"):
                                call.call_metadata["eval_started"] = True
                                call.save(update_fields=["call_metadata"])
                                _run_simulate_evaluations_task.apply_async(
                                    args=(call.id,)
                                )
                                logger.info(
                                    f"Started evaluations for call after completion {call.id}"
                                )
                else:
                    all_done = False
            test_execution = TestExecution.objects.filter(id=test_execution.id).get()
            if all_done:
                # Check if we need to run evaluations
                evaluations_pending = False

                call_executions = CallExecution.objects.filter(
                    test_execution=test_execution
                )

                for call in call_executions:
                    if call.status == CallExecution.CallStatus.COMPLETED:
                        # Check if evaluations have been completed for this call
                        if not call.call_metadata:
                            call.call_metadata = {}
                        if not call.call_metadata.get("eval_started"):
                            evaluations_pending = True
                            call.call_metadata["eval_started"] = True
                            call.save(update_fields=["call_metadata"])
                            _run_simulate_evaluations_task.apply_async(args=(call.id,))
                            logger.info(f"Started evaluations for call {call.id}")
                        elif not call.call_metadata.get("eval_completed"):
                            logger.info(
                                f"Evaluations already started for call {call.id}, waiting for completion"
                            )
                            evaluations_pending = True
                if evaluations_pending:
                    if test_execution.status not in [
                        TestExecution.ExecutionStatus.CANCELLED,
                        TestExecution.ExecutionStatus.CANCELLING,
                    ]:
                        # Set status to EVALUATING if we have pending evaluations
                        if (
                            test_execution.status
                            != TestExecution.ExecutionStatus.EVALUATING
                        ):
                            test_execution.status = (
                                TestExecution.ExecutionStatus.EVALUATING
                            )

                            logger.info(
                                f"Test execution {test_execution.id} set to EVALUATING status"
                            )

                    test_execution.picked_up_by_executor = (
                        False  # Release the execution
                    )
                    test_execution.save()

                else:
                    if test_execution.status not in [
                        TestExecution.ExecutionStatus.CANCELLED,
                        TestExecution.ExecutionStatus.CANCELLING,
                    ]:
                        # if test_execution.run_test.enable_tool_evaluation:
                        #     # Before marking completed, aggregate all tool columns from all call executions
                        #     self._aggregate_tool_columns_to_test_execution(test_execution)

                        # All evaluations are completed, mark test execution as completed
                        test_execution.status = TestExecution.ExecutionStatus.COMPLETED

                        test_execution.eval_explanation_summary_status = (
                            EvalExplanationSummaryStatus.PENDING
                        )
                        test_execution.save()
                        # Lazy import to avoid circular dependency
                        from simulate.tasks.eval_summary_tasks import (
                            run_eval_summary_task,
                        )

                        run_eval_summary_task.apply_async(
                            args=(str(test_execution.id),)
                        )

                    logger.info(
                        f"Test execution {test_execution.id} completed - all evaluations finished"
                    )
            else:
                test_execution.picked_up_by_executor = False
                test_execution.save()

        except Exception as e:
            test_execution.picked_up_by_executor = False
            test_execution.save()
            logger.error(f"Error in monitor_test_execution: {e}")

    def stop(self):
        """Stop the test executor service"""
        self.running = False
        logger.info("Test executor stopped")

    def execute_test(
        self, run_test_id: str, user_id: str, scenario_ids, simulator_id=None
    ) -> dict[str, Any]:
        """
        Execute a test run with all its scenarios

        Args:
            run_test_id: UUID of the RunTest to execute
            user_id: ID of the user executing the test

        Returns:
            Dict containing execution status and details
        """
        try:
            # Get the run test first (outside transaction)
            run_test = RunTest.objects.get(id=run_test_id)

            # Validate test is ready to run
            if not self._validate_test_ready(run_test):
                return {
                    "success": False,
                    "error": "Test is not ready to run",
                    "run_test_id": run_test_id,
                }

            # Get all scenarios for this test
            if not scenario_ids:
                return {
                    "success": False,
                    "error": "No scenarios found for this test",
                    "run_test_id": run_test_id,
                }

            # Create TestExecution record in a separate transaction
            try:
                # Get simulator agent and agent definition
                simulator_agent = None
                agent_definition = run_test.agent_definition
                agent_version = run_test.agent_version

                if simulator_id:
                    try:
                        simulator_agent = SimulatorAgent.objects.get(id=simulator_id)
                    except SimulatorAgent.DoesNotExist:
                        logger.warning(
                            f"Simulator agent not found: {simulator_id}, using default"
                        )
                        simulator_agent = run_test.simulator_agent
                else:
                    simulator_agent = run_test.simulator_agent

                test_execution_record = TestExecution.objects.create(
                    run_test=run_test,
                    status=TestExecution.ExecutionStatus.PENDING,
                    started_at=timezone.now(),
                    total_scenarios=len(scenario_ids),
                    scenario_ids=[str(sid) for sid in scenario_ids],
                    picked_up_by_executor=False,
                    simulator_agent=simulator_agent,
                    agent_definition=agent_definition,
                    agent_version=agent_version,
                )
            except Exception as e:
                logger.error(f"Error creating TestExecution record: {str(e)}")
                return {
                    "success": False,
                    "error": f"Failed to create test execution record: {str(e)}",
                    "run_test_id": run_test_id,
                }

            # Initialize test execution tracking
            test_execution = {
                "run_test_id": run_test_id,
                "execution_id": str(test_execution_record.id),
                "user_id": user_id,
                "start_time": timezone.now(),
                "status": "running",
                "scenarios": [],
                "total_calls": 0,
                "completed_calls": 0,
                "failed_calls": 0,
                "results": {},
            }

            # Collect all calls from all scenarios first
            all_calls_with_scenario = []
            scenario_objects = {}

            for scenario_id in scenario_ids:
                try:
                    scenario = Scenarios.objects.get(
                        id=scenario_id, organization=run_test.organization
                    )
                    scenario_objects[scenario_id] = scenario

                    row_ids = self._parse_dataset_scenario(scenario)
                    # Convert row IDs to call data format
                    calls_to_make = [
                        {"row_id": row_id, "metadata": {}} for row_id in row_ids
                    ]

                    # # Parse scenario source based on type
                    # if scenario.scenario_type == Scenarios.ScenarioTypes.DATASET:
                    #     row_ids = self._parse_dataset_scenario(scenario)
                    #     # Convert row IDs to call data format
                    #     calls_to_make = [{'row_id': row_id, 'metadata': {}} for row_id in row_ids]
                    # elif scenario.scenario_type == Scenarios.ScenarioTypes.SCRIPT:
                    #     calls_to_make = self._parse_script_scenario(scenario)
                    # elif scenario.scenario_type == Scenarios.ScenarioTypes.GRAPH:
                    #     calls_to_make = self._parse_graph_scenario(scenario)
                    # else:
                    #     raise ValueError(f"Unsupported scenario type: {scenario.scenario_type}")

                    # Add scenario info to each call
                    for call_data in calls_to_make:
                        all_calls_with_scenario.append(
                            {
                                "scenario_id": scenario_id,
                                "scenario": scenario,
                                "call_data": call_data,
                            }
                        )

                except Exception as e:
                    traceback.print_exc()
                    logger.error(f"Error parsing scenario {scenario_id}: {str(e)}")

            # # Shuffle and limit to max 5 calls
            # random.shuffle(all_calls_with_scenario)
            selected_calls = all_calls_with_scenario

            # Group selected calls by scenario
            calls_by_scenario: dict[Any, Any] = {}
            for call_info in selected_calls:
                scenario_id = call_info["scenario_id"]
                if scenario_id not in calls_by_scenario:
                    calls_by_scenario[scenario_id] = []
                calls_by_scenario[scenario_id].append(call_info["call_data"])

            # Execute each scenario with limited calls
            total_calls = 0
            for scenario_id in scenario_ids:
                try:
                    scenario = scenario_objects[scenario_id]
                    # Get the limited calls for this scenario
                    scenario_calls = calls_by_scenario.get(scenario_id, [])
                    scenario_result = self._execute_scenario_with_calls(
                        run_test,
                        scenario,
                        test_execution_record,
                        user_id,
                        scenario_calls,
                        simulator_id,
                    )
                    test_execution["scenarios"].append(scenario_result)
                    total_calls += scenario_result.get("total_calls", 0)
                except Exception as e:
                    traceback.print_exc()
                    logger.error(f"Error executing scenario {scenario_id}: {str(e)}")
                    scenario_obj = scenario_objects.get(scenario_id, {})
                    scenario_name = (
                        getattr(scenario_obj, "name", "Unknown")
                        if scenario_id in scenario_objects
                        else "Unknown"
                    )
                    scenario_result = {
                        "scenario_id": str(scenario_id),
                        "scenario_name": scenario_name,
                        "status": "failed",
                        "error": str(e),
                        "total_calls": 0,
                        "completed_calls": 0,
                        "failed_calls": 0,
                    }
                    test_execution_scenarios = test_execution.get("scenarios", [])
                    if isinstance(test_execution_scenarios, list):
                        test_execution_scenarios.append(scenario_result)

            # Update the TestExecution record with total calls
            try:
                test_execution_record.total_calls = total_calls
                test_execution_record.save()
            except Exception as e:
                traceback.print_exc()
                logger.error(f"Error updating TestExecution record: {str(e)}")

            test_execution["total_calls"] = total_calls

            # Store test execution in active tests
            # self.active_tests[run_test_id] = test_execution

            logger.info(
                f"Started test execution for {run_test_id} with {len(scenario_ids)} scenarios"
            )

            # Test execution will be picked up by the periodic monitor_test_executions Temporal schedule
            logger.info(
                f"Test execution {test_execution_record.id} created and will be picked up by Temporal monitor task"
            )

            return {
                "success": True,
                "run_test_id": run_test_id,
                "execution_id": str(test_execution_record.id),
                "status": "started",
                "total_scenarios": len(scenario_ids),
                "total_calls": total_calls,
            }

        except ObjectDoesNotExist:
            return {
                "success": False,
                "error": "Run test not found",
                "run_test_id": run_test_id,
            }
        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error executing test {run_test_id}: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to execute test: {str(e)}",
                "run_test_id": run_test_id,
            }

    def _validate_test_ready(self, run_test: RunTest) -> bool:
        """Validate that a test is ready to run"""
        try:
            # Check based on source type
            if run_test.source_type == RunTest.SourceTypes.PROMPT:
                # For prompt-based tests, check if prompt template or version exists
                if not run_test.prompt_template and not run_test.prompt_version:
                    return False
            else:
                # For agent_definition source type, check if agent definition exists
                if not run_test.agent_definition:
                    return False

            # agent_version is required for agent-definition based tests,
            # but not for prompt-based simulations
            if (
                run_test.source_type != RunTest.SourceTypes.PROMPT
                and not run_test.agent_version
            ):
                return False

            # Check if there are scenarios
            if not run_test.scenarios.exists():
                return False

            return True

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error validating test readiness: {str(e)}")
            return False

    def _execute_scenario_with_calls(
        self,
        run_test: RunTest,
        scenario: Scenarios,
        test_execution_record: TestExecution,
        user_id: str,
        calls_to_make: list[dict[str, Any]],
        simulator_id=None,
    ) -> dict[str, Any]:
        """
        Execute a single scenario with predefined calls

        Args:
            run_test: The RunTest instance
            scenario: The Scenarios instance to execute
            test_execution_record: The TestExecution record
            user_id: ID of the user executing the test
            calls_to_make: List of call data to execute

        Returns:
            Dict containing scenario execution details
        """
        try:
            scenario_result = {
                "scenario_id": str(scenario.id),
                "scenario_name": scenario.name,
                "scenario_type": scenario.scenario_type,
                "status": "running",
                "calls": [],
                "total_calls": len(calls_to_make),
                "completed_calls": 0,
                "failed_calls": 0,
                "start_time": timezone.now(),
                "end_time": None,
            }

            # Execute each call in the scenario
            for call_data in calls_to_make:
                call_result = self._execute_call(
                    run_test,
                    scenario,
                    call_data,
                    test_execution_record,
                    user_id,
                    simulator_id,
                )
                scenario_result["calls"].append(call_result)

                if call_result["success"]:
                    scenario_result["completed_calls"] += 1
                else:
                    scenario_result["failed_calls"] += 1

            scenario_result["status"] = "completed"
            scenario_result["end_time"] = timezone.now()

            return scenario_result

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error executing scenario {scenario.id}: {str(e)}")
            return {
                "scenario_id": str(scenario.id),
                "scenario_name": scenario.name,
                "status": "failed",
                "error": str(e),
                "total_calls": len(calls_to_make) if "calls_to_make" in locals() else 0,
                "completed_calls": 0,
                "failed_calls": (
                    len(calls_to_make) if "calls_to_make" in locals() else 0
                ),
            }

    def _parse_dataset_scenario(self, scenario: Scenarios) -> list[str]:
        """Parse dataset scenario to extract row IDs for dynamic prompts"""

        try:
            dataset = scenario.dataset
            rows = Row.objects.filter(dataset=dataset).order_by("order")

            # Return list of row IDs
            return [str(row.id) for row in rows]

        except Exception as e:
            logger.error(f"Error parsing dataset scenario {scenario.id}: {str(e)}")
            return []

    def _get_row_data_and_generate_prompt(
        self,
        row_id: str,
        base_prompt: str,
        agent_version: AgentVersion | None,
        call_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Get row data from dataset and generate dynamic prompt

        Args:
            row_id: ID of the Row from dataset
            base_prompt: The base prompt template from simulator agent

        Returns:
            Dict containing row data and dynamic prompt
        """
        try:
            # Get the row
            row = Row.objects.get(id=row_id)

            # Get all cells for this row
            cells = Cell.objects.filter(row=row)
            row_data = {}

            # Build row data dictionary
            for cell in cells:
                column_name = cell.column.name
                row_data[column_name] = cell.value

            # Generate dynamic prompt by substituting variables
            dynamic_prompt = self._generate_dynamic_prompt(
                base_prompt, row_data, agent_version, call_type
            )

            return {
                "row_data": row_data,
                "dynamic_prompt": dynamic_prompt,
                "row_id": row_id,
                "dataset_id": str(row.dataset.id),
            }

        except Exception as e:
            logger.error(f"Error getting row data for row {row_id}: {str(e)}")
            return {
                "row_data": {},
                "dynamic_prompt": base_prompt,
                "row_id": row_id,
                "dataset_id": None,
            }

    def _format_persona_voice_text(
        self,
        persona_data: Dict[str, Any],
        agent_version: AgentVersion | None,
        row_data: Dict[str, Any] = None,
        call_type: str = "inbound",
    ) -> str:
        """
        Format persona data into a structured, human-readable text format with behavioral guidance

        Args:
            persona_data: Dictionary containing persona attributes including:
                - Basic fields: name, gender, age_group, occupation, location
                - Behavioral: personality, communication_style, keywords
                - Speech: languages, accent, multilingual
                - metadata: arbitrary key-value pairs for additional context
            row_data: Optional dictionary containing row data (for situation context)
            call_type: Type of call - "inbound" (receiving call) or "outbound" (making call)

        Returns:
            Formatted persona text with sections for identity, personality, speech characteristics,
            and detailed behavioral guidance
        """
        sections = []

        # =================================================================
        # SECTION 1: IDENTITY & ROLE
        # =================================================================
        identity_parts = []
        name = persona_data.get("name", "")
        profession = persona_data.get("profession") or persona_data.get(
            "occupation", ""
        )
        location = persona_data.get("location", "")
        age_group = persona_data.get("age_group") or persona_data.get(
            "ageGroup", ""
        )  # ← FIX
        gender = persona_data.get("gender", "")

        if name:
            identity_parts.append(f"**Name:** {name}")
        if profession:
            identity_parts.append(f"**Occupation:** {profession}")
        if age_group:
            identity_parts.append(f"**Age Group:** {age_group}")
        if location:
            identity_parts.append(f"**Location:** {location}")
        if gender:
            identity_parts.append(f"**Gender:** {gender}")

        if identity_parts:
            sections.append("\n# YOUR IDENTITY\n\n" + "\n".join(identity_parts))

        # =================================================================
        # SECTION 2: CURRENT SITUATION & OBJECTIVE
        # =================================================================
        situation_section = "\n# YOUR CURRENT SITUATION\n\n"
        if row_data and row_data.get("situation"):
            situation_section += f"{row_data['situation']}\n\n"
            situation_section += (
                "**CRITICAL:** This situation describes your context and circumstances. "
                "You are EXPERIENCING this situation, not explaining it to others. "
                "Never narrate, describe, or mention the details of your situation to the other person unless they specifically ask. "
                "Act naturally within this context - your behavior should reflect the situation, not announce it.\n\n"
            )
        else:
            situation_section += "You are engaging in a routine conversation.\n\n"

        call_type_lower = call_type.lower() if call_type else "inbound"
        situation_section += "## Your Role in This Call\n\n"

        if call_type_lower == "outbound":
            # Agent is receiving the call (outbound from the system being tested)
            situation_section += (
                "**You are RECEIVING this call.** Someone is calling you.\n\n"
                "**CRITICAL: You did NOT initiate this call. You are the person being contacted.**\n\n"
                "Your behavior:\n"
                "- Answer the phone based on your personality and current situation\n"
                "- React naturally based on whether you were expecting this call\n"
                "- Let the caller introduce themselves and explain their purpose\n"
                "- YOU are the person being reached out to - respond from that position\n"
                "- Ask questions, express reactions, or raise concerns as this person would\n"
                "- NEVER switch roles and act as if you made the call or are providing the service\n"
                "**Name Verification:**\n"
                "- If the caller addresses you by the wrong name, correct them ONCE naturally\n"
                "- After your initial correction, do NOT keep correcting the name throughout the call\n"
                "- If they persist with the wrong name after your correction, you may show brief frustration\n\n"
                "- Don't let name correction dominate the entire interaction—move forward with the actual purpose of the call\n\n"
            )
        else:  # inbound
            # Agent is making the call (inbound to the system being tested)
            situation_section += (
                "**You are MAKING this call.** You initiated this contact.\n\n"
                "**CRITICAL: YOU started this conversation. You are reaching out to someone.**\n\n"
                "Your behavior:\n"
                "- State your purpose clearly\n"
                "- You have a specific reason for calling (based on your situation above)\n"
                "- YOU are seeking something - information, help, service, answers, etc.\n"
                "- Provide information when asked, answer questions, follow their guidance\n"
                "- NEVER switch roles and act as if you're the one receiving the call or providing assistance\n\n"
            )
        situation_section += (
            "**React Naturally:** Respond to what you hear in real-time. "
            "Interrupt politely if needed, ask clarifying questions, express confusion if something is unclear, "
            "or show enthusiasm when appropriate. Stay in YOUR role throughout the entire conversation. "
            "If the agent keeps interrupting or talking over you, react like a real human: pause, "
            "politely ask them to let you finish, or briefly acknowledge the interruption before continuing "
            "what you were saying.\n"
        )

        sections.append(situation_section)

        # =================================================================
        # SECTION 3: PERSONALITY & COMMUNICATION STYLE
        # =================================================================
        personality = persona_data.get("personality", "")
        communication_style = persona_data.get(
            "communication_style"
        ) or persona_data.get("communicationStyle", "")
        keywords = persona_data.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [x.strip() for x in keywords.split(",") if x.strip()]

        if (
            personality
            or communication_style
            or (isinstance(keywords, list) and keywords)
        ):
            personality_section = "# YOUR PERSONALITY & COMMUNICATION\n\n"

            # Personality trait with specific behavioral guidance
            # if personality is list or dict type, convert to string

            if personality:
                if isinstance(personality, list):
                    personality = personality[0]
                elif isinstance(personality, dict):
                    personality = list(personality.values())[0]
                personality_lower = personality.lower()
                personality_section += f"## Personality: {personality}\n\n"
                # Personality-specific guidance
                guidance = VOICE_PERSONALITY_GUIDES.get(
                    personality_lower,
                    f"Let this personality trait guide your reactions, responses, and overall demeanor.",
                )
                personality_section += f"{guidance}\n\n"

            # Communication style with specific guidance

            if communication_style:
                if isinstance(communication_style, list):
                    communication_style = communication_style[0]
                elif isinstance(communication_style, dict):
                    communication_style = list(communication_style.values())[0]
                comm_style_lower = communication_style.lower()
                personality_section += (
                    f"## Communication Style: {communication_style}\n\n"
                )

                # Communication style-specific guidance
                guidance = VOICE_COMMUNICATION_STYLE_GUIDES.get(
                    comm_style_lower,
                    f"Let this style guide how you express yourself throughout the conversation.",
                )
                personality_section += f"{guidance}\n\n"

            # Keywords/traits if present
            if isinstance(keywords, list) and keywords:
                keywords_str = ", ".join(str(k) for k in keywords)
                personality_section += f"**Key Traits:** {keywords_str}\n\n"
            sections.append(personality_section)

        # =================================================================
        # SECTION 4: LANGUAGE & ACCENT
        # =================================================================
        accent = persona_data.get("accent", "")
        language_data = persona_data.get("language") or persona_data.get("languages")
        if not language_data:
            version_snapshot = (
                getattr(agent_version, "configuration_snapshot", {}) or {}
            )
            language_data = version_snapshot.get("language") or version_snapshot.get(
                "languages"
            )

        if accent or language_data:
            language_section = "# LANGUAGE & SPEECH PATTERNS\n\n"
            section_has_content = False

            # Language(s)
            if language_data:
                section_has_content = True
                langs = (
                    language_data
                    if isinstance(language_data, list)
                    else [language_data]
                )
                lang_str = (
                    ", ".join(str(l) for l in langs)
                    if isinstance(langs, list)
                    else str(language_data)
                )
                language_section += f"**Language(s):** {lang_str}\n"
                language_section += f"Use vocabulary, expressions, and language patterns natural to someone who speaks {lang_str}.\n"

                # Special handling for multilingual contexts
                if persona_data.get("multilingual"):
                    language_section += f"You are multilingual. Switch languages naturally based on context while maintaining your persona traits in all languages.\n"

                # Special handling for Hinglish speakers
                # if (accent and "indian" in accent.lower()) or any("hindi" in str(l).lower() for l in langs): # operator precedence
                #     language_section += (
                #         "**Hinglish Context:** You naturally mix Hindi and English words/phrases where it feels conversational, "
                #         "just as someone from this background would. Don't force it—prioritize natural flow.\n"
                #     )

            # Accent
            if accent:
                #     language_section += f"**Accent:** {accent}\n"
                #     language_section += f"Reflect this accent in your speech patterns, word choices, and pronunciation. This is a core part of your identity.\n"

                if accent.lower() == "indian" and language_data:
                    langs = (
                        language_data
                        if isinstance(language_data, list)
                        else [language_data]
                    )
                    lang_str_check = (
                        ", ".join(str(l) for l in langs)
                        if isinstance(langs, list)
                        else str(language_data)
                    )
                    if lang_str_check.lower().startswith("en"):
                        section_has_content = True
                        language_section += (
                            "**Number Formatting Rules:**\n"
                            "- Always express numbers in words (e.g., 'fifty thousand' not '50,000')\n"
                            "- For sequences like phone numbers, say each digit separately (e.g., 'seven nine two eight' not '7928')\n"
                            "- Never give mobile numbers or pincodes in sequential order like 'one two three four five six'\n\n"
                        )
            if section_has_content:
                sections.append(language_section)

        # =================================================================
        # SECTION 5: CONTEXTUAL AWARENESS
        # =================================================================
        if age_group or profession or location:
            context_section = "# CONTEXTUAL AWARENESS\n\n"
            if age_group:
                context_section += (
                    f"**Age Context:** Your age group ({age_group}) influences your knowledge, cultural references, "
                    f"interests, and how you relate to topics. Respond with age-appropriate perspective and vocabulary.\n"
                )
            if profession:
                context_section += (
                    f"**Professional Context:** Your work as a {profession} shapes your priorities, problem-solving approach, "
                    f"and how you view situations. Reference your professional background when relevant.\n"
                )
            if location:
                context_section += (
                    f"**Geographic Context:** Being from {location} influences your cultural context, experiences, "
                    f"time zone awareness, and regional references. Use examples and perspectives from your location.\n\n"
                )

            sections.append(context_section)

        # =================================================================
        # SECTION 6: ADDITIONAL METADATA (if present)
        # =================================================================
        metadata = persona_data.get("metadata")
        if metadata and isinstance(metadata, dict):
            metadata_parts = []
            for key, value in metadata.items():
                formatted_key = key.replace("_", " ").title()
                metadata_parts.append(f"**{formatted_key}:** {value}")

            if metadata_parts:
                sections.append(
                    "# ADDITIONAL CHARACTERISTICS\n\n" + "\n".join(metadata_parts)
                )

        # =================================================================
        # SECTION 7: CORE SIMULATION RULES
        # =================================================================
        rules_section = "# HOW TO BE THIS PERSON\n\n"
        rules_section += "You ARE this person. Embody this character completely in every response.\n\n"
        rules_section += "## Core Rules\n\n"
        rules_section += "1. **Full Embodiment:** Every response must come from this character's perspective, background, and emotional state.\n"
        rules_section += "2. **Natural Speech Only:** Generate ONLY dialogue your character would say. Never include:\n"
        rules_section += "   - Stage directions (e.g., *sighs*, [anxious])\n"
        rules_section += "   - Meta-commentary or explanations\n"
        rules_section += "   - Labels or descriptions of your actions\n"
        rules_section += "3. **Unwavering Consistency:** Maintain your personality, communication style, and accent from start to finish. No exceptions.\n"
        rules_section += "4. **Contextual Authenticity:** Your knowledge, vocabulary, and references must match your age, profession, and location.\n"
        rules_section += "5. **Task-Driven Interaction:** Actively pursue your objective based on 'Your Current Situation.' Your persona dictates HOW you pursue it.\n"
        rules_section += "6. **Refocus When Drifting:** If you find yourself repeating phrases or losing track of your objective, refocus on your initial situation and goal.\n"
        rules_section += "7. **Authentic Reactions:** Respond as this specific person would—not how you think someone 'should' respond.\n"
        rules_section += (
            "8. **Natural Conversation Flow:** Respond naturally like a real human.\n"
        )
        rules_section += "9. **Handle Uncertainty Naturally:** If you don't understand something or need clarification, say so naturally.\n"
        rules_section += "10. **Never Break Character:** You are the PERSON described in 'Your Identity' with the situation in 'Your Current Situation.' You are NOT the person on the other end of the line. If you find yourself switching roles - taking on the other person's responsibilities, responding as if you have opposite information or authority, or reversing who called whom - STOP immediately. Stay in your role.\n"
        rules_section += "11. **Information Sharing:** Only share personal information when it's directly relevant to the conversation or when asked. Don't volunteer unnecessary details about yourself, your background, or your situation unless it naturally fits the context. Real people don't introduce themselves with their entire life story; be selective and purposeful with what you reveal.\n"
        rules_section += "12. **Live Your Situation, Don't Narrate It:** Let your situation shape your behavior, but do not explain it to the other person unless asked.\n"
        rules_section += (
            "13. **Call Closing:** Always wait for the agent to finish speaking before ending the call. "
            "Do not cut them off abruptly. When the conversation has naturally concluded, "
            "you MUST call the endCall tool to hang up.\n"
            'IMPORTANT: Never say the words "function", "tool" or the name "endCall" out loud. '
            "Never say that you are ending the call. Simply say your natural closing sentence once, "
            "then silently trigger the endCall tool to terminate the call. Do not leave the call open.\n"
            "CRITICAL: If the agent says goodbye, bye, take care, or any closing phrase, "
            "you MUST respond with a brief, natural closing sentence (e.g. 'Alright, thanks, bye!') "
            "and then call endCall. Do NOT keep exchanging goodbyes. If you find yourself repeating "
            "goodbye phrases, call endCall right away.\n\n"
        )

        sections.append(rules_section)
        # Combine all sections
        return "\n\n".join(sections)

    def _append_voice_execution_rules(self, prompt: str) -> str:
        prompt += "\n\n---\n\n"
        prompt += "# CONVERSATION EXECUTION RULES\n\n"
        prompt += "*These are internal instructions. Never reference or quote them in your responses.*\n\n"

        prompt += "## CRITICAL REMINDERS FOR THIS CONVERSATION\n\n"
        prompt += "Before each response, mentally confirm:\n"
        prompt += "✓ Am I speaking AS this person (not ABOUT them)?\n"
        prompt += "✓ Does this match my personality and communication style?\n"
        prompt += "✓ Am I using my accent and natural speech patterns?\n"
        prompt += "✓ Is this how someone with my background would actually respond?\n\n"

        prompt += "## Output Format\n\n"
        prompt += "Generate ONLY spoken dialogue without:\n"
        prompt += "- Emotional tags, action descriptions, quotation marks, or meta-commentary\n"
        prompt += "- Brackets, quotes, or markup\n\n"

        prompt += "## Sound Human\n\n"
        prompt += "- Use natural speech patterns including filler words (um, uh, well, like, you know) when appropriate\n"
        prompt += "- Don't be afraid of brief hesitations, self-corrections, or incomplete thoughts if that matches your personality\n"
        prompt += "- Real people don't speak in perfect grammatical sentences—neither should you\n\n"

        prompt += "## Voice-Natural Formatting (following are few examples on how to respond; use them as reference formats only)\n"
        prompt += "**Numbers:** 'fifty thousand' not '50,000'\n"
        prompt += "**Phone numbers:** 'eight nine seven one one five three six four' not '897115364'\n"
        prompt += (
            "**Dates:** 'November fourteenth twenty twenty five' not '11/14/2025'\n"
        )
        prompt += "**Currency:** 'twenty five dollars and fifty cents' not '$25.50'\n"
        prompt += "**Time:** 'three thirty PM' not '3:30 PM'\n"
        prompt += (
            "**Punctuation spacing:** Always add a space after punctuation before the next word "
            "(e.g., 'Thank you. I…' or 'Thank you.. I…', not 'Thank you.I…' or 'Thank you..I…').\n\n"
        )

        prompt += "## Embody Your Situation\n\n"
        prompt += "- Let the situation guide your behavior, not your narration\n"
        prompt += "- Only mention situational details if they naturally come up\n\n"

        prompt += "Be natural and conversational.\n"
        return prompt

    def _format_persona_chat_text(
        self,
        persona_data: Dict[str, Any],
        agent_version: AgentVersion | None,
        row_data: Dict[str, Any] = None,
        call_type: str = "inbound",
    ) -> str:
        """
        Format persona data into a structured text format optimized for chat interactions.

        Args:
            persona_data: Dictionary containing persona attributes including:
                - Basic fields: name, gender, age_group, occupation, location
                - Behavioral: personality, communication_style, keywords
                - Chat-specific: tone, verbosity, regional_mix, slang_level, typo_level,
                punctuation_style, emoji_frequency
                - metadata: arbitrary key-value pairs for additional context
            agent_version: Version of the agent being tested
            row_data: Optional dictionary containing row data (for situation context)
            call_type: Type of interaction - "inbound" (user messages agent) or "outbound" (agent messages user)

        Returns:
            Formatted persona text with sections for identity, personality, chat-specific behaviors,
            and detailed behavioral guidance
        """
        sections = []

        # =================================================================
        # SECTION 1: IDENTITY & ROLE
        # =================================================================
        identity_parts = []
        name = persona_data.get("name", "")
        profession = persona_data.get("profession") or persona_data.get(
            "occupation", ""
        )
        location = persona_data.get("location", "")
        age_group = persona_data.get("age_group") or persona_data.get("ageGroup", "")
        gender = persona_data.get("gender", "")
        if name:
            identity_parts.append(f"**Name:** {name}")
        if profession:
            identity_parts.append(f"**Occupation:** {profession}")
        if age_group:
            identity_parts.append(f"**Age Group:** {age_group}")
        if location:
            identity_parts.append(f"**Location:** {location}")
        if gender:
            identity_parts.append(f"**Gender:** {gender}")

        if identity_parts:
            sections.append("\n# YOUR IDENTITY\n\n" + "\n".join(identity_parts))

        # =================================================================
        # SECTION 2: CURRENT SITUATION & OBJECTIVE
        # =================================================================
        situation_section = "\n# YOUR CURRENT SITUATION\n\n"

        if row_data and row_data.get("situation"):
            situation_section += f"{row_data['situation']}\n\n"
        else:
            situation_section += "You are engaging in a routine chat conversation.\n\n"

        call_type_lower = call_type.lower() if call_type else "inbound"
        situation_section += "## Your Role in This Chat\n\n"

        if call_type_lower == "outbound":
            # Agent is receiving the message (outbound from the system being tested)
            situation_section += (
                "**You are RECEIVING this message.** Someone is reaching out to you via chat.\n\n"
                "**CRITICAL: You did NOT initiate this conversation. You are the person being contacted.**\n\n"
                "Your behavior:\n"
                "- Respond to the incoming message based on your personality and current situation\n"
                "- React naturally based on whether you were expecting this contact\n"
                "- Let the other person introduce themselves and explain their purpose\n"
                "- YOU are the person being reached out to - respond from that position\n"
                "- Ask questions, express reactions, or raise concerns as this person would\n"
                "- NEVER switch roles and act as if you initiated the conversation or are providing the service\n\n"
                "**Name Verification:**\n"
                "- If they address you by the wrong name, correct them ONCE naturally\n"
                "- After your initial correction, do NOT keep correcting the name throughout the chat\n"
                "- If they persist with the wrong name after your correction, you may show brief frustration\n"
                "- Don't let name correction dominate the entire interaction-move forward with the actual purpose of the chat\n\n"
            )
        else:  # inbound
            # Agent is initiating the message (inbound to the system being tested)
            situation_section += (
                "You initiated this chat to address the situation described above.\n\n"
                "- Share why you're reaching out (immediately or after a short opener, depending on your persona)\n"
                "- You are seeking help: answer questions and provide info as requested\n"
                "- Follow guidance when it makes sense for your situation\n"
                "- Do NOT switch roles (you are not the service/provider)\n\n"
            )
        situation_section += (
            "React naturally to what you read in real-time. "
            "Ask clarifying questions, express confusion if something is unclear, "
            "or show enthusiasm when appropriate. "
            "Let your situation and personality guide your responses. "
            "Stay consistent with your role throughout the conversation.\n"
        )

        sections.append(situation_section)

        # =================================================================
        # SECTION 3: PERSONALITY & COMMUNICATION STYLE
        # =================================================================
        personality = persona_data.get("personality", "")
        communication_style = persona_data.get(
            "communication_style"
        ) or persona_data.get("communicationStyle", "")
        keywords = persona_data.get("keywords", [])

        if isinstance(keywords, str):
            keywords = [x.strip() for x in keywords.split(",") if x.strip()]

        if (
            personality
            or communication_style
            or (isinstance(keywords, list) and keywords)
        ):
            personality_section = "# YOUR PERSONALITY & COMMUNICATION\n\n"
            # Personality trait with specific behavioral guidance
            # if personality is list or dict type, convert to string
            if personality:
                if isinstance(personality, list):
                    personality = personality[0]
                elif isinstance(personality, dict):
                    personality = list(personality.values())[0]

                personality_lower = personality.lower()
                personality_section += f"## Personality: {personality}\n\n"

                # Personality-specific guidance
                guidance = CHAT_PERSONALITY_GUIDES.get(
                    personality_lower,
                    f"Let this personality trait guide your reactions, responses, and overall messaging style.",
                )
                personality_section += f"{guidance}\n\n"

            # Communication style with specific guidance
            if communication_style:
                if isinstance(communication_style, list):
                    communication_style = communication_style[0]
                elif isinstance(communication_style, dict):
                    communication_style = list(communication_style.values())[0]

                comm_style_lower = communication_style.lower()
                personality_section += (
                    f"## Communication Style: {communication_style}\n\n"
                )

                # Communication style-specific guidance
                guidance = CHAT_COMMUNICATION_STYLE_GUIDES.get(
                    comm_style_lower,
                    f"Let this style guide how you express yourself throughout the chat conversation.",
                )
                personality_section += f"{guidance}\n\n"

            # Keywords/traits if present
            if isinstance(keywords, list) and keywords:
                keywords_str = ", ".join(str(k) for k in keywords)
                personality_section += f"**Key Traits:** {keywords_str}\n\n"

            sections.append(personality_section)

        # =================================================================
        # SECTION 4: CHAT-SPECIFIC WRITING STYLE
        # =================================================================
        defaults_applied: dict[str, str] = {}

        def _normalize_persona_str(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, list):
                return _normalize_persona_str(value[0]) if value else ""
            if isinstance(value, dict):
                return _normalize_persona_str(next(iter(value.values()), ""))
            return str(value).strip()

        def _get_with_default(
            *keys: str,
            default_value: str,
            canonical_key: str,
        ) -> str:
            raw = None
            for key in keys:
                if not key:
                    continue
                candidate = persona_data.get(key)
                if (
                    candidate is not None
                    and candidate != ""
                    and candidate != []
                    and candidate != {}
                ):
                    raw = candidate
                    break
            normalized = _normalize_persona_str(raw)
            if not normalized:
                defaults_applied[canonical_key] = default_value
                return default_value
            return normalized

        # Defaults (human-ish): casual + short, chat-natural writing.
        # Stress-test option: tone="casual", verbosity="detailed", regional_mix/slang/emoji="heavy", typo="frequent", punctuation="erratic".
        tone = _get_with_default("tone", default_value="casual", canonical_key="tone")
        verbosity = _get_with_default(
            "verbosity", default_value="brief", canonical_key="verbosity"
        )
        regional_mix = _get_with_default(
            "regional_mix",
            "regionalMix",
            default_value="none",
            canonical_key="regional_mix",
        )
        slang_level = _get_with_default(
            # Primary keys
            "slang_usage",
            "slangUsage",
            # Legacy keys
            "slang_level",
            "slangLevel",
            default_value="light",
            canonical_key="slang_usage",
        )
        typo_level = _get_with_default(
            # Primary keys
            "typos_frequency",
            "typosFrequency",
            # Legacy keys
            "typo_level",
            "typoLevel",
            default_value="rare",
            canonical_key="typos_frequency",
        )
        punctuation_style = _get_with_default(
            # Primary keys
            "punctuation",
            # Legacy keys
            "punctuation_style",
            "punctuationStyle",
            default_value="minimal",
            canonical_key="punctuation",
        )
        emoji_frequency = _get_with_default(
            # Primary keys
            "emoji_usage",
            "emojiUsage",
            # Legacy keys
            "emoji_frequency",
            "emojiFrequency",
            default_value="light",
            canonical_key="emoji_usage",
        )

        if defaults_applied:
            logger.debug(
                "chat_persona_defaults_applied",
                agent_version_id=str(getattr(agent_version, "id", "")),
                call_type=call_type,
                defaults=defaults_applied,
            )

        if any(
            [
                tone,
                verbosity,
                regional_mix,
                slang_level,
                typo_level,
                punctuation_style,
                emoji_frequency,
            ]
        ):
            chat_style_section = "# YOUR CHAT WRITING STYLE\n\n"

            # Tone
            if tone:
                tone_lower = tone.lower()
                chat_style_section += f"## Tone: {tone.title()}\n\n"

                guidance = CHAT_TONE_GUIDES.get(
                    tone_lower, f"Use a {tone} tone in your messages."
                )
                chat_style_section += f"{guidance}\n\n"

            # Verbosity
            if verbosity:
                verbosity_lower = verbosity.lower()
                chat_style_section += f"## Message Length: {verbosity.title()}\n\n"

                guidance = CHAT_VERBOSITY_GUIDES.get(
                    verbosity_lower, f"Use {verbosity} messages."
                )
                chat_style_section += f"{guidance}\n\n"

            # Regional Mix
            if regional_mix:
                regional_lower = regional_mix.lower()
                chat_style_section += (
                    f"## Regional Language Mixing: {regional_mix.title()}\n\n"
                )

                guidance = CHAT_REGIONAL_MIX_GUIDES.get(
                    regional_lower, f"Use {regional_mix} regional language mixing."
                )
                chat_style_section += f"{guidance}\n\n"

            # Slang Level
            if slang_level:
                slang_lower = slang_level.lower()
                chat_style_section += f"## Slang Usage: {slang_level.title()}\n\n"

                guidance = CHAT_SLANG_LEVEL_GUIDES.get(
                    slang_lower, f"Use {slang_level} slang."
                )
                chat_style_section += f"{guidance}\n\n"

            # Typo Level
            if typo_level:
                typo_lower = typo_level.lower()
                chat_style_section += f"## Typing Accuracy: {typo_level.title()}\n\n"

                guidance = CHAT_TYPO_LEVEL_GUIDES.get(
                    typo_lower, f"Type with {typo_level} typos."
                )
                chat_style_section += f"{guidance}\n\n"

            # Punctuation Style
            if punctuation_style:
                punct_lower = punctuation_style.lower()
                chat_style_section += (
                    f"## Punctuation Style: {punctuation_style.title()}\n\n"
                )

                guidance = CHAT_PUNCTUATION_STYLE_GUIDES.get(
                    punct_lower, f"Use {punctuation_style} punctuation."
                )
                chat_style_section += f"{guidance}\n\n"

            # Emoji Frequency
            if emoji_frequency:
                emoji_lower = emoji_frequency.lower()
                chat_style_section += f"## Emoji Usage: {emoji_frequency.title()}\n\n"

                guidance = CHAT_EMOJI_FREQUENCY_GUIDES.get(
                    emoji_lower, f"Use emojis at {emoji_frequency} frequency."
                )
                chat_style_section += f"{guidance}\n\n"

            sections.append(chat_style_section)

        # =================================================================
        # SECTION 5: LANGUAGE & CULTURAL CONTEXT
        # =================================================================
        language_data = persona_data.get("language") or persona_data.get("languages")
        if not language_data:
            version_snapshot = (
                getattr(agent_version, "configuration_snapshot", {}) or {}
            )
            language_data = version_snapshot.get("language") or version_snapshot.get(
                "languages"
            )

        if language_data:
            language_section = "# LANGUAGE & CULTURAL CONTEXT\n\n"

            langs = (
                language_data if isinstance(language_data, list) else [language_data]
            )
            lang_str = (
                ", ".join(str(l) for l in langs)
                if isinstance(langs, list)
                else str(language_data)
            )

            language_section += f"**Language(s):** {lang_str}\n"
            language_section += f"Use vocabulary, expressions, and language patterns natural to someone who communicates in {lang_str}. Your word choices, idioms, and cultural references should reflect this language background.\n"

            # Special handling for multilingual contexts
            if persona_data.get("multilingual"):
                language_section += f"You are multilingual. Switch languages naturally based on context while maintaining your persona traits in all languages.\n"

            language_section += "\n"
            sections.append(language_section)

        # =================================================================
        # SECTION 6: CONTEXTUAL AWARENESS
        # =================================================================
        if age_group or profession or location:
            context_section = "# CONTEXTUAL AWARENESS\n\n"

            if age_group:
                context_section += (
                    f"**Age Context:** Your age group ({age_group}) influences your knowledge, cultural references, "
                    f"internet/texting habits, and how you communicate. Reflect age-appropriate perspective, vocabulary, and digital communication patterns.\n"
                )

            if profession:
                context_section += (
                    f"**Professional Context:** Your work as a {profession} shapes your priorities, problem-solving approach, "
                    f"and how you view situations. Reference your professional background when relevant, and let it influence your communication style.\n"
                )

            if location:
                context_section += (
                    f"**Geographic Context:** Being from {location} influences your cultural context, experiences, "
                    f"time zone awareness, and regional references. Use examples and perspectives from your location. Your spelling and terminology may reflect regional variations.\n\n"
                )

            sections.append(context_section)

        # =================================================================
        # SECTION 7: ADDITIONAL METADATA (if present)
        # =================================================================
        metadata = persona_data.get("metadata")
        if metadata and isinstance(metadata, dict):
            metadata_parts = []
            for key, value in metadata.items():
                formatted_key = key.replace("_", " ").title()
                metadata_parts.append(f"**{formatted_key}:** {value}")

            if metadata_parts:
                sections.append(
                    "# ADDITIONAL CHARACTERISTICS\n\n" + "\n".join(metadata_parts)
                )

        # =================================================================
        # SECTION 8: CORE SIMULATION RULES
        # =================================================================
        rules_section = "# HOW TO BE THIS PERSON IN CHAT\n\n"
        rules_section += "You ARE this person. Embody their identity, situation, and goals in every message.\n\n"
        rules_section += "## Core Rules\n\n"
        rules_section += "1. Stay in role: speak only from this character's perspective; do not switch roles or act as the provider.\n"
        rules_section += "2. Match your persona consistently: tone, vocabulary, slang/typos/emojis, and message length; reflect your age/profession/location.\n"
        rules_section += "3. Stay goal-driven: pursue your objective from your current situation; refocus if the chat drifts.\n"
        rules_section += "4. Natural chat only: output just the message text (no meta, no stage directions, no emotion labels).\n"
        rules_section += "5. React like a human: ask clarifying questions when needed; show confusion/enthusiasm naturally.\n"
        rules_section += "6. Human typing mindset: minimum effort to communicate; don't polish messages like an assistant; avoid repetitive politeness; end the chat naturally when done.\n\n"

        sections.append(rules_section)

        # Combine all sections
        return "\n\n".join(sections)

    def _append_chat_execution_rules(self, prompt: str) -> str:
        """
        Append general execution rules for chat-based conversations.
        These rules apply universally regardless of persona or situation.

        Args:
            prompt: The base prompt to append rules to

        Returns:
            The prompt with chat execution rules appended
        """
        prompt += "\n\n---\n\n"
        prompt += "# CHAT EXECUTION RULES\n\n"
        prompt += "*Internal instructions: never reference or quote them.*\n\n"

        prompt += "## Hard Constraints (Follow Exactly)\n\n"
        prompt += "- You are the CUSTOMER in this scenario. Your job is to solve your own situation, not to help, teach, or evaluate the agent.\n"
        prompt += "- Output ONLY the next chat message text. No headings, no lists, no bullets, no multi-paragraph replies, no quotes, no meta-commentary.\n"
        prompt += "- One message at a time: ask or answer ONE thing per message. If unsure, ask one direct clarifying question.\n"
        prompt += "- Stay human: write like quick phone texting with minimum effort. Do not write polished/professional emails.\n"
        prompt += "- Never sound like an assistant: do not provide generic explanations, step-by-step guides, policy/security/legal lectures, or "
        prompt += "phrases like 'for evaluation purposes', 'I'd be happy to help', 'please confirm the requirements', or long formal greetings.\n"
        prompt += "- If asked about internal systems/security/laws/policies you wouldn't realistically know, say you don't know/not sure and steer back to your goal.\n"
        prompt += "- Follow the persona settings (tone/verbosity/slang/typos/punctuation/emojis), but prefer shorter and less-polished phrasing over perfect writing.\n\n"

        prompt += "## Realistic Personal Details\n\n"
        prompt += "- If asked for personal details (phone/email/address fragments/IDs), provide plausible fictional fully-specified values (make them up if needed).\n"
        prompt += "- Do NOT use masked placeholders like '98xxxxxxx2', '9XXXXXXXXX', '***', or 'XXX-XXX'.\n"
        prompt += "- STRICT: Never use obvious fake patterns (sequential runs, repeated digits, keyboard patterns). Avoid: '9876543210', '1234567890', '1111111111', '0000000000', 'abc123', 'asdf', 'qwerty'.\n"
        prompt += "- Keep any invented details consistent and plausible for your persona/location.\n\n"

        prompt += "## Conversation Closing\n\n"
        prompt += "- Always wait for the reply from the other side before ending the chat. Do not cut them off abruptly.\n"
        prompt += "- When the conversation is MUTUALLY finished (both sides have said goodbye/thanks and there's nothing left to discuss), you can trigger the endCall function.\n"
        prompt += "- Do NOT end the conversation after just 1-2 exchanges. Have a meaningful back-and-forth conversation first.\n"
        prompt += '- Never type the words "function", "tool", or "endCall" in your message. Simply send your natural closing message, then silently trigger the endCall function.\n\n'

        return prompt

    def _generate_dynamic_prompt(
        self,
        prompt_template: str,
        row_data: Dict[str, Any],
        agent_version: AgentVersion | None,
        call_type: str | None = None,
    ) -> str:
        """
        Generate dynamic prompt by substituting variables from row data.
        Args:
            prompt_template: The prompt template with variables like {{persona}}, {{situation}}
            row_data: Dictionary containing row data from dataset
            agent_version: Version of the agent being tested (None for prompt-based simulations)
            call_type: Type of call - "inbound" or "outbound"
        Returns:
            The final prompt with variables substituted and conversation rules applied
        """

        try:
            # Handle prompt-based simulations where agent_version is None
            if agent_version is None:
                # For prompt-based simulations, default to chat/text type and inbound
                version_snapshot = {}
                resolved_inbound = True
                resolved_call_type = call_type or "inbound"
                resolved_agent_type = "text"
            else:
                version_snapshot = (
                    getattr(agent_version, "configuration_snapshot", {}) or {}
                )
                raw_inbound = version_snapshot.get("inbound", None)
                if raw_inbound is None:
                    raw_inbound = getattr(
                        agent_version.agent_definition, "inbound", True
                    )
                if isinstance(raw_inbound, str):
                    resolved_inbound = raw_inbound.strip().lower() == "true"
                else:
                    resolved_inbound = bool(raw_inbound)

                resolved_call_type = call_type or (
                    "inbound" if resolved_inbound else "outbound"
                )
                resolved_agent_type = (
                    version_snapshot.get("agent_type")
                    or version_snapshot.get("agentType")
                    or getattr(agent_version.agent_definition, "agent_type", None)
                )
            logger.debug(
                "dynamic_prompt_builder_context",
                call_type=resolved_call_type,
                agent_type=resolved_agent_type,
            )
            # Create a copy of the template
            dynamic_prompt = prompt_template

            # Handle persona variable specially
            if "{{persona}}" in dynamic_prompt and "persona" in row_data:
                persona = row_data["persona"]

                # Parse persona if it's a string
                if isinstance(persona, str):
                    try:
                        persona_data = json.loads(persona)
                    except json.JSONDecodeError:
                        try:
                            import ast

                            persona_data = ast.literal_eval(persona)
                        except (ValueError, SyntaxError) as e:
                            logger.warning(
                                f"Failed to parse persona string: {e}. Using empty persona."
                            )
                            persona_data = {}
                elif isinstance(persona, dict):
                    persona_data = persona
                else:
                    persona_data = {}

                # Format persona into structured text
                if str(resolved_agent_type).lower() in {"text", "chat"}:
                    persona_text = self._format_persona_chat_text(
                        persona_data=persona_data,
                        agent_version=agent_version,
                        row_data=row_data,
                        call_type=resolved_call_type,
                    )
                else:
                    persona_text = self._format_persona_voice_text(
                        persona_data=persona_data,
                        agent_version=agent_version,
                        row_data=row_data,
                        call_type=resolved_call_type,
                    )
                dynamic_prompt = dynamic_prompt.replace("{{persona}}", persona_text)
                # We already inject the situation inside persona_text, so scrub any user-templated {{situation}}.
                # 1) Remove "Currently, {{situation}}" (case-insensitive, optional comma/period, tolerant spacing)
                dynamic_prompt = re.sub(
                    r"(?i)\s*(?:\.\s*)?currently\s*,?\s*\{\{situation\}\}\s*(?:\.\s*)?",
                    "\n\n",
                    dynamic_prompt,
                )
                # 2) Fallback: remove any remaining bare "{{situation}}" with optional surrounding space/periods
                dynamic_prompt = re.sub(
                    r"\s*(?:\.\s*)?\{\{situation\}\}\s*(?:\.\s*)?",
                    "\n\n",
                    dynamic_prompt,
                )
                # 3) Polish: collapse spaces and fix punctuation spacing
                dynamic_prompt = re.sub(
                    r"[ \t]{2,}", " ", dynamic_prompt
                )  # collapse multiple spaces
                dynamic_prompt = re.sub(
                    r"\s+([,.!?;:])", r"\1", dynamic_prompt
                )  # no space before punctuation
                dynamic_prompt = dynamic_prompt.strip()  # trim ends

            # Replace other variables in the template with actual values from row_data
            for key, value in row_data.items():
                if key in {"persona", "situation"}:  # Already handled keys
                    continue
                placeholder = f"{{{{{key}}}}}"
                if placeholder in dynamic_prompt:
                    dynamic_prompt = dynamic_prompt.replace(placeholder, str(value))

            # Log the substitution for debugging
            # logger.info(f"Generated dynamic prompt: {dynamic_prompt[:100]}")

            # =================================================================
            # FINAL INSTRUCTIONS: CONVERSATION ENFORCEMENT
            # =================================================================
            if str(resolved_agent_type).lower() in {"text", "chat"}:
                dynamic_prompt = self._append_chat_execution_rules(dynamic_prompt)
            else:
                dynamic_prompt = self._append_voice_execution_rules(dynamic_prompt)

            logger.info(
                "dynamic_prompt_generated",
                call_type=resolved_call_type,
                agent_type=resolved_agent_type,
                prompt_length=len(dynamic_prompt),
            )

            return dynamic_prompt

        except Exception as e:
            logger.error(f"Error generating dynamic prompt: {str(e)}")
            return prompt_template  # Return original template if substitution fails

    def _parse_script_scenario(self, scenario: Scenarios) -> list[dict[str, Any]]:
        """Parse script scenario to extract call data"""
        calls = []

        try:
            # For script scenarios, parse the script content
            # This could be a structured format with multiple calls
            import json

            try:
                data = json.loads(scenario.source)
                if isinstance(data, list):
                    calls = data
                elif isinstance(data, dict) and "calls" in data:
                    calls = data["calls"]
                else:
                    calls = [data]
            except json.JSONDecodeError:
                # Try parsing as a simple script format
                lines = scenario.source.strip().split("\n")
                for line in lines:
                    if line.strip() and not line.startswith("#"):
                        # Parse script format: metadata (phone number comes from agent definition)
                        parts = line.split("|")
                        if len(parts) >= 1:
                            # Try to parse metadata as JSON, otherwise treat as string
                            metadata_str = parts[0].strip() if len(parts) > 0 else "{}"
                            try:
                                import json

                                metadata = (
                                    json.loads(metadata_str) if metadata_str else {}
                                )
                            except json.JSONDecodeError:
                                # If not valid JSON, treat as a simple string
                                metadata = (
                                    {"data": metadata_str} if metadata_str else {}
                                )

                            call_data = {"metadata": metadata}
                            calls.append(call_data)

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error parsing script scenario {scenario.id}: {str(e)}")
            calls = []

        return calls

    def _parse_graph_scenario(self, scenario: Scenarios) -> list[dict[str, Any]]:
        """Parse graph scenario to extract call data"""
        calls = []

        try:
            # For graph scenarios, parse the graph structure
            # This could be a flow diagram converted to call sequences
            import json

            try:
                data = json.loads(scenario.source)
                if isinstance(data, list):
                    calls = data
                elif isinstance(data, dict):
                    # Extract calls from graph structure
                    if "nodes" in data and "edges" in data:
                        # Convert graph to call sequence
                        calls = self._convert_graph_to_calls(data)
                    elif "calls" in data:
                        calls = data["calls"]
                    else:
                        calls = [data]
                else:
                    calls = [data]

            except json.JSONDecodeError:
                logger.error(f"Invalid JSON in graph scenario {scenario.id}")
                calls = []

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error parsing graph scenario {scenario.id}: {str(e)}")
            calls = []

        return calls

    def _convert_graph_to_calls(
        self, graph_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Convert graph structure to call sequence"""
        calls = []

        try:
            nodes = graph_data.get("nodes", [])
            graph_data.get("edges", [])

            # Simple conversion: each node becomes a call
            for node in nodes:
                if node.get("type") == "call":
                    call_data = {
                        "phone_number": node.get("phone_number", ""),
                        "metadata": node.get("metadata", {}),
                        "node_id": node.get("id"),
                    }
                    calls.append(call_data)

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error converting graph to calls: {str(e)}")

        return calls

    def _check_call_balance(
        self,
        organization,
        call_type: Optional[str] = SimulationCallType.VOICE,
    ):
        """Check whether an organization is allowed to run a simulation call.

        Primary caller is the chat (TEXT) simulation path in
        ``simulate.services.chat_sim.initiate_chat``. Voice simulations run
        through the Temporal ``call_execution_workflow``, which performs
        its own ``check_call_balance`` activity and does not invoke
        ``TestExecutor``. The ``VOICE`` branch here is retained as a
        defensive fallback for the legacy ``_execute_call`` voice path,
        which is no longer exercised in production.

        Delegates to the postpaid metering system
        (``usage.services.metering.check_usage``) which enforces the
        7-dimensional free-tier caps and budget-pause flags. Paid plans
        (PAYG, Boost, Scale, Enterprise, Custom) always pass — usage is
        settled via metered Stripe invoicing at the end of the billing
        period. The legacy ``wallet_balance`` gate has been retired.

        Requires Redis — ``check_usage`` is Redis-backed. If Redis is
        unavailable the ``except`` branch fails closed (returns
        ``allowed=False``), which is the safer default.

        Args:
            organization: Organization object.
            call_type: ``SimulationCallType.TEXT`` for chat sims,
                ``SimulationCallType.VOICE`` for the vestigial voice
                fallback.

        Returns:
            tuple: ``(allowed, current_balance, estimated_cost, error_message)``.
            ``current_balance`` and ``estimated_cost`` are retained as 0 for
            back-compat with existing callers; the metered model does not
            expose a prepaid balance.
        """
        try:
            event_type = (
                "text_call"
                if call_type == SimulationCallType.TEXT
                else "voice_call"
            )
            result = check_usage(str(organization.id), event_type)

            if result.allowed:
                return True, 0, 0, None

            return False, 0, 0, result.reason or "Usage limit exceeded"

        except Exception as e:
            logger.error(f"Error checking call balance: {str(e)}")
            return False, 0, 0, f"Error checking balance: {str(e)}"

    def _validate_call_prerequisites(
        self, run_test: RunTest, scenario: Scenarios, call_data: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Validate prerequisites before executing a call

        Args:
            run_test: The RunTest instance
            scenario: The Scenarios instance
            call_data: Data for this specific call

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            # Check if organization exists
            if not run_test.organization:
                return False, "Organization not found for this test run"

            # For prompt-based simulations, skip agent_definition and phone checks
            is_prompt_based = run_test.source_type == RunTest.SourceTypes.PROMPT
            if is_prompt_based:
                # For prompt-based simulations, we only need organization and prompt_template
                if not run_test.prompt_template:
                    return (
                        False,
                        "Prompt template not found for this prompt-based simulation",
                    )
                return True, ""

            # Check if agent definition exists (for agent-definition based simulations)
            if not run_test.agent_definition:
                return False, "Agent definition not found for this test run"

            # Check if phone number is configured (for voice simulations)
            if run_test.agent_version and run_test.agent_version.configuration_snapshot:
                if not run_test.agent_version.configuration_snapshot.get(
                    "contact_number"
                ):
                    return (
                        False,
                        "Phone number not configured in this version of agent definition",
                    )

            # # Check if simulator agent exists
            # if not run_test.simulator_agent:
            #     return False, "Simulator agent not found for this test run"

            # # Check if prompt is configured
            # if not run_test.simulator_agent.prompt:
            #     return False, "System prompt not configured in simulator agent"

            return True, ""

        except Exception as e:
            logger.error(f"Error validating call prerequisites: {str(e)}")
            return False, f"Error validating prerequisites: {str(e)}"

    def _validate_outbound_call_prerequisites(
        self, run_test: RunTest
    ) -> tuple[bool, str]:
        """
        Validate prerequisites specific to outbound calls
        Includes all basic checks plus outbound-specific requirements

        Args:
            run_test: The RunTest instance

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            if not run_test.organization:
                return False, "Organization not found for this test run"

            if not run_test.agent_definition:
                return False, "Agent definition not found for this test run"

            agent_definition = run_test.agent_definition
            agent_version = (
                run_test.agent_version
                if run_test.agent_version
                else agent_definition.latest_version
            )
            snapshot = agent_version.configuration_snapshot

            if not snapshot.get("api_key"):
                return (
                    False,
                    "API key required for outbound calls. Please configure api_key in agent definition.",
                )

            if not snapshot.get("assistant_id"):
                return (
                    False,
                    "Assistant ID required for outbound calls. Please configure assistant_id in agent definition.",
                )

            if not snapshot.get("contact_number"):
                return (
                    False,
                    "Phone number required for outbound calls. Please configure contact_number in agent definition.",
                )

            return True, None

        except Exception as e:
            logger.error(f"Error validating outbound call prerequisites: {str(e)}")
            return False, f"Error validating outbound prerequisites: {str(e)}"

    def _execute_call(
        self,
        run_test: RunTest,
        scenario: Scenarios,
        call_data: Dict[str, Any],
        test_execution_record: TestExecution,
        user_id: str,
        simulator_id=None,
    ) -> Dict[str, Any]:
        """
        Execute an inbound call where simulation agent calls user's agent (existing logic)

        Args:
            run_test: The RunTest instance
            scenario: The Scenarios instance
            call_data: Data for this specific call (contains row_id for dataset scenarios)
            test_execution_record: The TestExecution record
            user_id: ID of the user executing the test
            simulator_id: ID of the simulator agent to use (optional)
        Returns:
            Dict containing call execution details
        """
        try:
            # Step 1: Get the relevant agent definition, agent version and related snapshot configuration
            is_prompt_based = run_test.source_type == RunTest.SourceTypes.PROMPT
            agent_definition = run_test.agent_definition

            if is_prompt_based:
                # For prompt-based simulations, use TEXT agent type and no phone
                # agent_definition and agent_version are None for prompt-based
                selected_version = None
                snapshot = {}
                agent_type = CallExecution.SimulationCallType.TEXT
                phone_number = None
            else:
                selected_version = (
                    run_test.agent_version
                    if run_test.agent_version
                    else agent_definition.latest_version
                )
                if not selected_version and agent_definition:
                    selected_version = agent_definition.latest_version

                snapshot = (
                    selected_version.configuration_snapshot if selected_version else {}
                )
                agent_type = (
                    snapshot.get("agent_type")
                    or snapshot.get("agentType")
                    or CallExecution.SimulationCallType.VOICE
                )
                phone_number = snapshot.get("contact_number")

            logger.info(f"phone_number from agent definition: {phone_number}")

            if not scenario.simulator_agent:
                scenario.refresh_from_db()

            # Step 2: Validate the pre-requisites for the call

            is_valid, validation_error = self._validate_call_prerequisites(
                run_test, scenario, call_data
            )
            inbound = snapshot.get("inbound", True)

            if not inbound:
                is_valid, validation_error = self._validate_outbound_call_prerequisites(
                    run_test
                )

            if not is_valid:
                test_execution_record.status = TestExecution.ExecutionStatus.FAILED
                test_execution_record.error_reason = validation_error
                test_execution_record.save()
                return {
                    "phone_number": "",
                    "metadata": call_data.get("metadata", {}),
                    "status": "failed",
                    "service_provider_call_id": None,
                    "start_time": None,
                    "end_time": None,
                    "duration": None,
                    "success": False,
                    "error": validation_error,
                }

            simulator_agent = (
                scenario.simulator_agent
                if scenario.simulator_agent
                else run_test.simulator_agent
            )

            if not simulator_agent:
                validated_data: dict[str, Any] = {}
                # Generate fallback prompt based on source type
                if is_prompt_based:
                    # For prompt-based simulations, use a generic prompt with the template name
                    prompt_name = (
                        run_test.prompt_template.name
                        if run_test.prompt_template
                        else "Agent"
                    )
                    fallback_prompt = (
                        f"You are a customer testing the '{prompt_name}' agent.\n\n"
                        "Your persona:\n{{persona}}\n\n"
                        "Your situation:\n{{situation}}\n\n"
                        "You initiated this chat to address the situation described above.\n\n"
                        "- Share your reason for reaching out naturally\n"
                        "- Answer their questions and provide information as they request it\n"
                        "- Follow their guidance as appropriate to your situation\n"
                    )
                else:
                    fallback_prompt = generate_simulator_agent_prompt(
                        agent_version=selected_version
                    )
                simulator_agent = SimulatorAgent.objects.create(
                    name=scenario.name,
                    prompt=fallback_prompt,
                    voice_provider=validated_data.get("voice_provider", "elevenlabs"),
                    voice_name=validated_data.get("voice_name", "marissa"),
                    model=validated_data.get("model", "gpt-4"),
                    llm_temperature=validated_data.get("llm_temperature", 0.7),
                    initial_message=validated_data.get("initial_message", "Hi!"),
                    max_call_duration_in_minutes=validated_data.get(
                        "max_call_duration_in_minutes", 30
                    ),
                    interrupt_sensitivity=validated_data.get(
                        "interrupt_sensitivity", 0.5
                    ),
                    conversation_speed=validated_data.get("conversation_speed", 1.0),
                    finished_speaking_sensitivity=validated_data.get(
                        "finished_speaking_sensitivity", 0.5
                    ),
                    initial_message_delay=validated_data.get(
                        "initial_message_delay", 0
                    ),
                    organization=scenario.organization,
                    workspace=scenario.workspace,
                )
                scenario.simulator_agent = simulator_agent
                scenario.save()

            base_prompt = simulator_agent.prompt
            row_data_info = {}
            system_prompt = base_prompt

            # Step 3: Generate dynamic prompt and get the row data
            if scenario.dataset and call_data.get("row_id"):
                row_data_info = self._get_row_data_and_generate_prompt(
                    call_data["row_id"],
                    base_prompt,
                    selected_version,
                )
                system_prompt = row_data_info.get("dynamic_prompt", base_prompt)
                logger.info(
                    f"Using dynamic prompt for row {call_data['row_id']}: {system_prompt[:100]}..."
                )
            logger.info(f"Call data received: {call_data}")

            call_execution_phone_number = phone_number if inbound else ""

            # Step 4: Create CallExecution record
            try:
                persona_data = row_data_info.get("persona")
                call_execution_metadata = {
                    "call_direction": "inbound",
                    "row_id": call_data.get("row_id"),
                    "row_data": row_data_info.get("row_data", {}),
                    "dataset_id": row_data_info.get("dataset_id"),
                    "base_prompt": base_prompt,
                    "agent_description": (
                        snapshot.get("description") if snapshot else None
                    ),
                    "dynamic_prompt": row_data_info.get("dynamic_prompt"),
                    "language": "en",
                    "initial_message": simulator_agent.initial_message,
                    "voice_name": simulator_agent.voice_name,
                    "conversation_speed": simulator_agent.conversation_speed,
                    "system_prompt": system_prompt,
                }

                if not inbound:
                    call_execution_metadata["call_direction"] = "outbound"
                    call_execution_metadata["user_assistant_id"] = snapshot.get(
                        "assistant_id"
                    )
                    call_execution_metadata["user_phone_number"] = (
                        call_execution_phone_number
                    )
                call_execution_metadata.update(call_data.get("metadata", {}))

                # Add prompt-based metadata
                if is_prompt_based:
                    call_execution_metadata["source_type"] = "prompt"
                    call_execution_metadata["prompt_template_id"] = (
                        str(run_test.prompt_template.id)
                        if run_test.prompt_template
                        else None
                    )
                    call_execution_metadata["prompt_version_id"] = (
                        str(run_test.prompt_version.id)
                        if run_test.prompt_version
                        else None
                    )

                # Use REGISTERED status for TEXT simulations so the
                # process_prompt_based_chat_simulations activity picks them up.
                # Voice simulations use PENDING (picked up by Temporal workflow).
                initial_status = (
                    CallExecution.CallStatus.REGISTERED
                    if is_prompt_based
                    else CallExecution.CallStatus.PENDING
                )

                call_execution = CallExecution.objects.create(
                    test_execution=test_execution_record,
                    scenario=scenario,
                    phone_number=call_execution_phone_number,
                    status=initial_status,
                    call_metadata=call_execution_metadata,
                    simulation_call_type=agent_type,
                    row_id=call_data.get("row_id"),
                    agent_version=selected_version,
                )
            except Exception as e:
                traceback.print_exc()
                logger.error(f"Error creating CallExecution record: {str(e)}")
                return {
                    "phone_number": call_execution_phone_number,
                    "metadata": call_data.get("metadata", {}),
                    "status": "failed",
                    "service_provider_call_id": None,
                    "start_time": None,
                    "end_time": None,
                    "duration": None,
                    "success": False,
                    "error": f"Failed to create call execution record: {str(e)}",
                }

            # For TEXT simulations (prompt-based), skip Vapi call creation
            # Chat simulations are initiated via SDK endpoint instead
            if agent_type == CallExecution.SimulationCallType.TEXT:
                logger.info(
                    f"TEXT simulation - CallExecution {call_execution.id} created with status REGISTERED. "
                    "Chat will be initiated via SDK endpoint."
                )
                return {
                    "call_execution_id": str(call_execution.id),
                    "phone_number": None,
                    "metadata": call_data.get("metadata", {}),
                    "status": "registered",
                    "vapi_call_id": None,
                    "start_time": None,
                    "end_time": None,
                    "duration": None,
                    "success": True,
                    "error": None,
                }

            # Check balance before proceeding with the call (voice simulations only)
            organization = run_test.organization

            # Step 5: Check if the customer has sufficient balance to make the calls
            has_sufficient_balance, current_balance, estimated_cost, balance_error = (
                self._check_call_balance(organization)
            )

            if not has_sufficient_balance:
                # Update call execution status to failed
                call_execution.status = CallExecution.CallStatus.FAILED
                call_execution.ended_reason = (
                    balance_error[:10000] if balance_error else "Insufficient balance"
                )
                call_execution.ended_at = timezone.now()
                call_execution.save()

                logger.warning(
                    f"Insufficient balance for call {call_execution.id}: {balance_error}"
                )

                return {
                    "call_execution_id": str(call_execution.id),
                    "phone_number": phone_number,
                    "metadata": call_data.get("metadata", {}),
                    "status": "failed",
                    "service_provider_call_id": None,
                    "start_time": None,
                    "end_time": None,
                    "duration": None,
                    "success": False,
                    "error": balance_error,
                }

            logger.info(
                f"Balance check passed for call {call_execution.id} "
                f"(organization: {organization.name})"
            )

            call_result = {
                "call_execution_id": str(call_execution.id),
                "phone_number": phone_number,
                "metadata": call_data.get("metadata", {}),
                "status": "pending",
                "service_provider_call_id": None,
                "start_time": None,
                "end_time": None,
                "duration": None,
                "success": False,
                "error": None,
            }

            # Step 6: Prepare voice settings for simulation agent
            row_data = row_data_info.get("row_data", {})

            # Parse persona field if it's a string representation of a dict
            persona_data = {}
            if "persona" in row_data:
                persona_value = row_data["persona"]
                if isinstance(persona_value, str):
                    try:
                        import ast

                        persona_data = ast.literal_eval(persona_value)
                    except (ValueError, SyntaxError) as e:
                        logger.warning(
                            f"Failed to parse persona string: {e}. Using empty persona."
                        )
                        # If parsing fails, use empty dict
                        persona_data = {}
                elif isinstance(persona_value, dict):
                    persona_data = persona_value

            # Select voice_id based on persona using LLM
            selected_voice_id = select_voice_id(persona_data)

            bg_sound = persona_data.get("background_sound") or persona_data.get(
                "backgroundSound"
            )
            bg_enabled = self._background_sound_enabled(bg_sound)
            bg_value = "on" if bg_enabled else "off"
            bg_reason = (
                "background enabled" if bg_enabled else "background disabled by persona"
            )

            voice_settings = {
                "voice_id": selected_voice_id,  # Map persona to voice_id
                "speed": persona_data.get(
                    "conversation_speed", simulator_agent.conversation_speed
                ),  # Map conversation_speed to speed
                "interrupt_sensitivity": persona_data.get(
                    "interrupt_sensitivity", simulator_agent.interrupt_sensitivity
                ),
                "finished_speaking_sensitivity": (
                    persona_data.get("finished_speaking_sensitivity")
                    if persona_data.get("finished_speaking_sensitivity") is not None
                    else (
                        persona_data.get("finishedSpeakingSensitivity")
                        if persona_data.get("finishedSpeakingSensitivity") is not None
                        else simulator_agent.finished_speaking_sensitivity
                    )
                ),
                "max_call_duration_in_minutes": simulator_agent.max_call_duration_in_minutes,
                "initial_message_delay": (
                    persona_data.get("initial_message_delay")
                    if persona_data.get("initial_message_delay") is not None
                    else (
                        persona_data.get("initialMessageDelay")
                        if persona_data.get("initialMessageDelay") is not None
                        else simulator_agent.initial_message_delay
                    )
                ),
                "language": (
                    persona_data.get("language", "en") if persona_data else "en"
                ),
                "initial_message": simulator_agent.initial_message,
                "background_sound": bg_value,
                "background_sound_reason": bg_reason,
                "conversation_speed": (
                    persona_data.get("conversation_speed")
                    if persona_data.get("conversation_speed") is not None
                    else (
                        persona_data.get("conversationSpeed")
                        if persona_data.get("conversationSpeed") is not None
                        else simulator_agent.conversation_speed
                    )
                ),
            }

            call_execution.call_metadata["voice_settings"] = voice_settings
            call_execution.save()

            # Step 7: Prepare metadata
            metadata = {
                "run_test_id": str(run_test.id),
                "scenario_id": str(scenario.id),
                "scenario_name": scenario.name,
                "user_id": user_id,
                "agent_definition_id": str(agent_definition.id),
                "organization_id": str(run_test.organization.id),
                "row_id": call_data.get("row_id"),
                "row_data": row_data_info.get("row_data", {}),
                "dataset_id": row_data_info.get("dataset_id"),
                "base_prompt": base_prompt,
                "dynamic_prompt": row_data_info.get("dynamic_prompt"),
            }

            if not inbound:
                metadata["call_direction"] = "outbound"
                metadata["user_assistant_id"] = snapshot.get("assistant_id")
                metadata["user_phone_number"] = call_execution_phone_number
                metadata["user_api_key"] = snapshot.get("api_key")

            # Ensure call_data metadata is a dictionary
            call_metadata = call_data.get("metadata", {})
            if not isinstance(call_metadata, dict):
                logger.info(
                    f"Call metadata is not a dictionary: {call_metadata}, converting to dict"
                )
                call_metadata = {"data": str(call_metadata)} if call_metadata else {}

            metadata.update(call_metadata)

            phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")
            if call_execution_phone_number and call_execution_phone_number.startswith(
                "+91"
            ):
                phone_number_id = VAPI_INDIAN_PHONE_NUMBER_ID or os.getenv(
                    "VAPI_PHONE_NUMBER_ID"
                )

            if not inbound:
                phone_number_id = ""

            # Debug logging
            logger.info(f"Creating call with phone_number_id: {phone_number_id}")
            logger.info(f"Creating call with to_number: {phone_number}")
            if row_data_info.get("dynamic_prompt"):
                logger.info(
                    f"Using dynamic prompt: {row_data_info['dynamic_prompt'][:100]}..."
                )

            try:
                # Step 5: Create CreateCallExecution record to queue the outbound call
                # Phone number for outbound call will be acquired in the background task when available
                CreateCallExecution.objects.create(
                    call_execution=call_execution,
                    phone_number_id=phone_number_id,
                    to_number=call_execution_phone_number,
                    system_prompt=system_prompt,
                    metadata=metadata,
                    voice_settings=voice_settings,
                )
                logger.info(f"Queued call execution for scenario {scenario.name}")
                # This is then picked up in test_monitor's create_call_execution temporal activity. It is scheduled to run every minute.

            except Exception as e:
                logger.error(f"Failed to create call: {str(e)}")
                traceback.print_exc()

                # Update call execution status to failed
                call_execution.status = CallExecution.CallStatus.FAILED
                error_msg = f"{str(e)}"
                call_execution.ended_reason = error_msg[:10000]
                call_execution.ended_at = timezone.now()
                call_execution.save()

                return {
                    "phone_number": call_execution_phone_number,
                    "metadata": call_data.get("metadata", {}),
                    "status": "failed",
                    "service_provider_call_id": None,
                    "start_time": None,
                    "end_time": None,
                    "duration": None,
                    "success": False,
                    "error": f"{str(e)}",
                }

            # Step 8: Update CallExecution record
            call_execution.status = CallExecution.CallStatus.REGISTERED
            call_execution.started_at = timezone.now()
            call_execution.save()

            # Update call result with response
            call_result["status"] = CallExecution.CallStatus.REGISTERED
            call_result["start_time"] = timezone.now()
            call_result["success"] = True

            return call_result
        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error executing call: {str(e)}")

            # Initialize call_result if it doesn't exist
            if "call_result" not in locals():
                call_result = {
                    "call_execution_id": (
                        str(call_execution.id) if "call_execution" in locals() else None
                    ),
                    "phone_number": phone_number if "phone_number" in locals() else "",
                    "metadata": (
                        call_data.get("metadata", {}) if "call_data" in locals() else {}
                    ),
                    "status": "failed",
                    "service_provider_call_id": None,
                    "start_time": None,
                    "end_time": None,
                    "duration": None,
                    "success": False,
                    "error": str(e),
                }
            else:
                call_result["status"] = "failed"
                call_result["error"] = str(e)
                call_result["success"] = False

            return call_result

    def get_test_status(self, run_test_id: str) -> dict[str, Any]:
        """
        Get the current status of a test execution

        Args:
            run_test_id: UUID of the RunTest

        Returns:
            Dict containing test execution status
        """
        if run_test_id in self.active_tests:
            return self.active_tests[run_test_id]
        else:
            # Check database for completed/failed test executions
            try:
                test_execution = (
                    TestExecution.objects.filter(run_test_id=run_test_id)
                    .order_by("-created_at")
                    .first()
                )

                if test_execution:
                    # Get call executions for this test
                    call_executions = CallExecution.objects.filter(
                        test_execution=test_execution
                    )

                    total_calls = call_executions.count()
                    completed_calls = call_executions.filter(
                        status__in=[
                            CallExecution.CallStatus.ANALYZING,
                            CallExecution.CallStatus.COMPLETED,
                        ]
                    ).count()
                    failed_calls = call_executions.filter(
                        status=CallExecution.CallStatus.FAILED
                    ).count()

                    return {
                        "run_test_id": run_test_id,
                        "execution_id": str(test_execution.id),
                        "status": test_execution.status,
                        "total_scenarios": test_execution.total_scenarios,
                        "total_calls": total_calls,
                        "completed_calls": completed_calls,
                        "failed_calls": failed_calls,
                        "success_rate": (
                            (completed_calls / total_calls * 100)
                            if total_calls > 0
                            else 0
                        ),
                        "start_time": test_execution.started_at,
                        "end_time": test_execution.completed_at,
                        "message": f"Test execution {test_execution.status} with {completed_calls} successful and {failed_calls} failed calls",
                    }
                else:
                    return {
                        "run_test_id": run_test_id,
                        "status": "not_found",
                        "error": "Test execution not found in database",
                    }
            except Exception as e:
                logger.error(f"Error getting test status from database: {str(e)}")
                return {
                    "run_test_id": run_test_id,
                    "status": "error",
                    "error": f"Error retrieving test status: {str(e)}",
                }

    def get_all_active_tests(self) -> dict[str, dict[str, Any]]:
        """
        Get all currently active test executions

        Returns:
            Dict of active test executions
        """
        return self.active_tests.copy()

    def cancel_test(
        self, run_test_id: str | None = None, test_execution_id: str | None = None
    ) -> dict[str, Any]:
        """
        Cancel an active test execution

        Args:
            run_test_id: UUID of the RunTest to cancel (cancels latest execution)
            test_execution_id: UUID of specific TestExecution to cancel

        Returns:
            Dict containing cancellation status
        """
        try:
            test_execution_record = TestExecution.objects.filter(
                id=test_execution_id
            ).get()

            logger.info(f"Cancelling test execution {test_execution_record.id}")

            # 1. Cancel all CreateCallExecution records (prevents new calls from being created)
            create_call_executions = CreateCallExecution.objects.filter(
                call_execution__test_execution=test_execution_record,
                status__in=[
                    CreateCallExecution.CallStatus.PENDING,
                    CreateCallExecution.CallStatus.REGISTERED,
                ],
            )

            cancelled_create_calls = create_call_executions.update(
                status=CreateCallExecution.CallStatus.CANCELLED
            )
            logger.info(
                f"Cancelled {cancelled_create_calls} pending CreateCallExecution records"
            )

            # 2. Cancel all active CallExecution records
            call_executions = CallExecution.objects.filter(
                test_execution=test_execution_record,
                status__in=[
                    CallExecution.CallStatus.PENDING,
                    CallExecution.CallStatus.REGISTERED,
                    CallExecution.CallStatus.ONGOING,
                ],
            )

            # Release phone numbers for outbound calls before cancelling (if phone was acquired)

            for call_execution in call_executions:
                try:
                    is_outbound = (
                        call_execution.call_metadata.get("call_direction") == "outbound"
                    )
                    if is_outbound:
                        # Only release if phone was actually acquired (check if phone_id exists in metadata)
                        simulation_phone_id = call_execution.call_metadata.get(
                            "simulation_phone_id"
                        )
                        if simulation_phone_id:
                            PhoneNumberService.release_phone_number(simulation_phone_id)
                            logger.info(
                                f"Released phone number for cancelled call {call_execution.id}"
                            )
                except Exception as e:
                    logger.error(
                        f"Error releasing phone for cancelled call {call_execution.id}: {str(e)}"
                    )

            cancelled_calls = 0
            failed_cancellations = 0
            call_executions.update(
                status=CallExecution.CallStatus.CANCELLED,
                ended_reason="Cancelled by user",
            )

            logger.info(
                f"Cancelled {cancelled_calls} CallExecution records, {failed_cancellations} failed"
            )

            # 3. Update TestExecution status
            test_execution_record.status = TestExecution.ExecutionStatus.CANCELLED
            test_execution_record.completed_at = timezone.now()
            test_execution_record.picked_up_by_executor = False
            test_execution_record.save()

            logger.info(
                f"Successfully cancelled test execution {test_execution_record.id}"
            )

            for call_execution in call_executions:
                try:
                    # If call has a service_provider_call_id and is ongoing, try to cancel it via API
                    if call_execution.service_provider_call_id:
                        try:
                            provider_payload = None
                            if call_execution.provider_call_data:
                                provider_payload = (
                                    call_execution.provider_call_data.get(
                                        str(self.system_voice_provider.value)
                                    )
                                )
                            from ee.voice.services.types.voice import EndCallInput

                            self.voice_service_manager.end_call(
                                EndCallInput(provider_call_payload=provider_payload)
                            )
                            logger.info(
                                f"Cancelled provider call {call_execution.service_provider_call_id}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to cancel provider call {call_execution.service_provider_call_id}: {str(e)}"
                            )
                            # Continue anyway to mark as cancelled in database

                except Exception as e:
                    logger.error(
                        f"Error cancelling call execution {call_execution.id}: {str(e)}"
                    )
                    traceback.print_exc()
                    failed_cancellations += 1

            return {
                "success": True,
                "test_execution_id": str(test_execution_record.id),
                "run_test_id": str(test_execution_record.run_test_id),
                "status": "cancelled",
                "cancelled_calls": cancelled_calls,
                "cancelled_create_calls": cancelled_create_calls,
                "failed_cancellations": failed_cancellations,
            }

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error cancelling test: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to cancel test: {str(e)}",
                "run_test_id": run_test_id,
                "test_execution_id": test_execution_id,
            }

    def _fetch_and_store_transcript(self, call_execution_id: str):
        """
        Fetch transcript and store it in CallTranscript model
        Uses appropriate credentials based on call direction

        Args:
            call_execution_id: The CallExecution ID
        """
        try:
            call_execution = CallExecution.objects.get(id=call_execution_id)
            if not call_execution.service_provider_call_id:
                logger.info(f"No call ID for call execution {call_execution.id}")
                return

            # Determine call direction and use appropriate service
            is_outbound = (
                call_execution.call_metadata.get("call_direction") == "outbound"
            )

            if is_outbound:
                # Use user's credentials for outbound calls
                agent_def = call_execution.test_execution.run_test.agent_definition
                agent_version = (
                    call_execution.agent_version
                    if call_execution.agent_version
                    else agent_def.latest_version
                )
                snapshot = agent_version.configuration_snapshot
                voice_service_manager = VoiceServiceManager(
                    api_key=snapshot.get("api_key")
                )
            else:
                # Use system credentials for inbound calls
                voice_service_manager = self.voice_service_manager

            # Fetch transcript
            transcript_object = voice_service_manager.get_call(
                call_execution.service_provider_call_id, True
            ).transcript.get(self.system_voice_provider)
            transcript_data = transcript_object.get("transcripts")

            if transcript_data:
                # Delete existing transcripts to avoid duplicates
                CallTranscript.all_objects.filter(
                    call_execution=call_execution
                ).delete()

                # Store new transcripts
                for item in transcript_data:
                    try:
                        # Use the actual timing data from Vapi
                        start_time_ms = item.get("start_time_ms", 0)
                        end_time_ms = item.get("end_time_ms", 0)

                        # Map speaker role to our choices
                        speaker_role = item.get("speaker_role", "unknown")
                        if speaker_role == "user":
                            speaker_role = CallTranscript.SpeakerRole.USER
                        elif speaker_role == "bot":
                            speaker_role = CallTranscript.SpeakerRole.ASSISTANT
                        elif speaker_role == "system":
                            speaker_role = CallTranscript.SpeakerRole.SYSTEM
                        elif speaker_role == "tool_calls":
                            speaker_role = CallTranscript.SpeakerRole.TOOL_CALLS
                        elif speaker_role == "tool_call_result":
                            speaker_role = CallTranscript.SpeakerRole.TOOL_CALL_RESULT
                        else:
                            speaker_role = CallTranscript.SpeakerRole.UNKNOWN

                        CallTranscript.objects.create(
                            call_execution=call_execution,
                            speaker_role=speaker_role,
                            content=item.get("content", ""),
                            start_time_ms=start_time_ms,
                            end_time_ms=end_time_ms,
                            confidence_score=item.get("confidence_score", 1.0),
                        )
                    except Exception as e:
                        logger.error(f"Error creating transcript record: {str(e)}")
                        traceback.print_exc()

                # Perform branch analysis
                analyzer = BranchDeviationAnalyzer()
                analysis = analyzer.analyze_call_execution_branch(call_execution)

                # Format response
                response_data = {
                    "new_nodes": analysis.new_nodes,
                    "new_edges": analysis.new_edges,
                    "current_path": analysis.current_path,
                    "expected_path": analysis.expected_path,
                    "analysis_summary": analysis.analysis_summary,
                }

                if not call_execution.analysis_data:
                    call_execution.analysis_data = {}
                call_execution.analysis_data["branch_analysis"] = response_data
                call_execution.save(update_fields=["analysis_data"])
                logger.info(
                    f"Stored {len(transcript_data)} transcript items for call {call_execution.id}"
                )
            else:
                logger.info(f"No transcript available for call {call_execution.id}")

        except Exception as e:
            logger.error(
                f"Error fetching transcript for call {call_execution.id}: {str(e)}"
            )
            traceback.print_exc()

    def _store_complete_call_data(self, call_execution_id: str):
        """
        Store complete call data including transcript, recording, cost, and performance metrics
        Uses appropriate credentials based on call direction (inbound vs outbound)

        Args:
            call_execution_id: UUID of the CallExecution
        """
        try:
            close_old_connections()
            call_execution = CallExecution.objects.get(id=call_execution_id)

            if not call_execution.service_provider_call_id:
                logger.info(f"No call ID for call execution {call_execution.id}")
                return

            if not call_execution.call_metadata:
                call_execution.call_metadata = {}
            # Mark that call data storage has been initiated (provider-agnostic internal flag)
            call_execution.refresh_from_db()

            agent_def = call_execution.test_execution.agent_definition
            resolved_agent_version = call_execution.agent_version
            if not resolved_agent_version and agent_def:
                resolved_agent_version = agent_def.latest_version
            configuration_snapshot = (
                resolved_agent_version.configuration_snapshot
                if resolved_agent_version
                else {}
            )
            customer_api_key = (
                configuration_snapshot.get("api_key")
                if configuration_snapshot
                else None
            )
            customer_assistant_id = (
                configuration_snapshot.get("assistant_id")
                if configuration_snapshot
                else None
            )

            # Determine call direction and use appropriate service
            is_outbound = (
                call_execution.call_metadata.get("call_direction") == "outbound"
            )

            if is_outbound:
                # Use customer's credentials for outbound calls
                if not customer_api_key:
                    logger.error("Outbound call missing customer API key")
                    return
                voice_service_manager = VoiceServiceManager(api_key=customer_api_key)
                logger.info(
                    f"Using user's credentials for outbound call {call_execution.id}"
                )
            else:
                # Use system VAPI credentials for inbound calls
                voice_service_manager = self.voice_service_manager
                logger.info(
                    f"Using system VAPI credentials for inbound call {call_execution.id}"
                )

            # Fetch complete call data from service provider
            try:
                call_data = voice_service_manager.get_call(
                    call_execution.service_provider_call_id, True
                )
                call_execution.status = call_data.status
            except Exception as e:
                error_message = str(e)
                logger.error(f"Error fetching call data: {error_message}")

                # Mark call as failed for any error
                try:
                    call_execution.status = CallExecution.CallStatus.FAILED
                    call_execution.ended_reason = f"Error: {error_message[:10000]}"  # Truncate long error messages
                    call_execution.ended_at = timezone.now()
                    call_execution.call_metadata = call_execution.call_metadata or {}
                    call_execution.call_metadata["error"] = error_message
                    call_execution.call_metadata["failure_reason"] = "api_error"
                    call_execution.save(
                        update_fields=[
                            "status",
                            "ended_reason",
                            "ended_at",
                            "call_metadata",
                        ]
                    )
                    logger.info(
                        f"Call {call_execution.id} marked as failed due to error: {error_message}"
                    )
                except Exception as save_error:
                    logger.error(f"Error marking call as failed: {str(save_error)}")
                    traceback.print_exc()

                return

            # Update CallExecution with complete data
            try:
                # Store complete Vapi call data

                customer_call_data = None
                customer_call_id: str | None = None

                if customer_api_key and customer_assistant_id:
                    if is_outbound:
                        customer_call_id = call_execution.service_provider_call_id
                    else:
                        try:
                            from ee.voice.services.types.voice import (
                                FindClientCallInput,
                            )

                            customer_call_id = self.voice_service_manager.find_client_call(
                                FindClientCallInput(
                                    customer_api_key=customer_api_key,
                                    customer_assistant_id=customer_assistant_id,
                                    our_call_data=call_data,
                                    customer_voice_service_provider=configuration_snapshot.get(
                                        "provider"
                                    ),
                                    time_window_seconds=10,
                                )
                            )
                        except Exception as e:
                            logger.warning("Unable to locate matching customer call ID")

                    if customer_call_id:
                        try:
                            customer_call_data = voice_service_manager.get_call(
                                customer_call_id, True
                            )
                        except Exception as e:
                            logger.warning("Failed to fetch customer call data")

                if customer_call_data:
                    self._store_customer_call_artifacts(
                        call_execution=call_execution,
                        customer_call_id=customer_call_id,
                        call_data=customer_call_data,
                    )

                # Store basic call information
                if call_data.assistant_id:
                    call_execution.assistant_id = call_data.assistant_id

                if call_data.customer_phone_number:
                    call_execution.customer_number = call_data.customer_phone_number

                if call_data.call_type:
                    call_execution.call_type = (
                        call_data.call_type.value
                        if hasattr(call_data.call_type, "value")
                        else call_data.call_type
                    )

                # Store timing information
                if call_data.started_at:
                    try:
                        from datetime import datetime

                        started_at_str = call_data.started_at
                        # Handle both ISO format and other formats
                        if "T" in started_at_str:
                            call_execution.started_at = datetime.fromisoformat(
                                started_at_str.replace("Z", "+00:00")
                            )
                        else:
                            call_execution.started_at = datetime.fromisoformat(
                                started_at_str
                            )
                    except Exception as e:
                        logger.info(
                            f"Could not parse startedAt: {call_data.started_at}, error: {e}"
                        )

                if call_data.ended_at:
                    try:
                        from datetime import datetime

                        ended_at_str = call_data.ended_at
                        # Handle both ISO format and other formats
                        if "T" in ended_at_str:
                            call_execution.ended_at = datetime.fromisoformat(
                                ended_at_str.replace("Z", "+00:00")
                            )
                            call_execution.completed_at = datetime.fromisoformat(
                                ended_at_str.replace("Z", "+00:00")
                            )
                            call_execution.duration_seconds = (
                                call_execution.completed_at - call_execution.started_at
                            ).total_seconds()
                        else:
                            call_execution.ended_at = datetime.fromisoformat(
                                ended_at_str
                            )
                            call_execution.completed_at = datetime.fromisoformat(
                                ended_at_str
                            )
                            call_execution.duration_seconds = (
                                call_execution.completed_at - call_execution.started_at
                            ).total_seconds()
                    except Exception as e:
                        logger.info(
                            f"Could not parse endedAt: {call_data.ended_at}, error: {e}"
                        )

                call_execution.recording_available = call_data.recording_available

                # Store raw provider payload under provider key (provider_call_data is validated to only allow provider keys)
                provider_key = str(voice_service_manager.system_voice_provider.value)
                raw_payload = call_data.raw_log
                if isinstance(raw_payload, dict):
                    # Keep provider_call_data schema-compliant: only provider keys at top-level
                    call_execution.provider_call_data = raw_payload
                call_execution.recording_url = call_data.recording_url
                call_execution.call_summary = call_data.summary

                # Store ended reason
                if call_data.ended_reason:
                    if call_data.ended_reason == "customer-did-not-answer":
                        call_execution.duration_seconds = 0
                        call_execution.status = CallExecution.CallStatus.FAILED
                    ended_reason = call_data.ended_reason
                    call_execution.ended_reason = (
                        ended_reason[:10000] if ended_reason else None
                    )

                # Store cost information
                call_execution.cost_cents = int(float(call_data.cost) * 100)

                if call_data.cost_breakdown.get(self.system_voice_provider):
                    cost_breakdown = call_data.cost_breakdown.get(
                        self.system_voice_provider
                    )
                    call_execution.stt_cost_cents = int(
                        float(cost_breakdown.get("stt", 0)) * 100
                    )
                    call_execution.llm_cost_cents = int(
                        float(cost_breakdown.get("llm", 0)) * 100
                    )
                    call_execution.tts_cost_cents = int(
                        float(cost_breakdown.get("tts", 0)) * 100
                    )
                    call_execution.vapi_cost_cents = int(
                        float(cost_breakdown.get("vapi", 0)) * 100
                    )

                # Store analysis and evaluation data
                if call_data.analysis_data.get(self.system_voice_provider):
                    # Extract performance metrics from analysis
                    analysis = call_data.analysis_data.get(self.system_voice_provider)
                    call_execution.analysis_data = analysis

                    # Convert successEvaluation to number (VAPI returns string 'true'/'false' or numeric score)

                    try:
                        if call_execution.recording_available:
                            if call_execution.recording_url:
                                from ee.voice.services.types.voice import (
                                    PersistAudioInput,
                                )

                                s3_url = self.voice_service_manager.persist_audio_to_s3(
                                    PersistAudioInput(
                                        call_id=call_execution.service_provider_call_id,
                                        audio_url=call_execution.recording_url,
                                        url_type="recording",
                                    )
                                )

                            recording_url = [s3_url]
                            csat = {
                                "name": "csat_score",
                                "description": "Evaluates the Customer Satisfaction (CSAT) score for a call between the customer and the agent.",
                                "criteria": "Assess the overall satisfaction expressed by the customer during the interaction. Consider explicit statements (e.g., 'thank you, this was helpful', 'this is frustrating') as well as implicit behavioral cues such as tone, cooperation, politeness, engagement, or dissatisfaction. Assign a single CSAT score from 1 to 10, where 1 indicates very dissatisfied and 10 indicates very satisfied. Only use evidence present in the interaction; do not infer beyond what is clearly communicated.",
                                "choices": [
                                    "1",
                                    "2",
                                    "3",
                                    "4",
                                    "5",
                                    "6",
                                    "7",
                                    "8",
                                    "9",
                                    "10",
                                ],
                                "multi_choice": False,
                            }
                            evaluator = DeterministicEvaluator(
                                multi_choice=csat["multi_choice"],
                                choices=csat["choices"],
                                rule_prompt=csat["criteria"],
                                input=recording_url,
                                input_type=["audio"],
                            )
                            result = evaluator._evaluate()
                            try:
                                csat_score = result.get("data", [])[0]
                                call_execution.overall_score = float(csat_score)
                                logger.debug(
                                    "csat_evaluation_result",
                                    call_execution_id=str(call_execution.id),
                                    csat_score=csat_score,
                                )

                            except:
                                logger.warning(
                                    "csat_evaluation_parse_failed",
                                    call_execution_id=str(call_execution.id),
                                )
                                success_eval = analysis.get("successEvaluation")
                                if success_eval is not None:
                                    if isinstance(success_eval, str):
                                        # Convert string 'true'/'false' to number
                                        if success_eval.lower() == "true":
                                            call_execution.overall_score = 1.0
                                        elif success_eval.lower() == "false":
                                            call_execution.overall_score = 0.0
                                        else:
                                            # Try to parse as numeric string
                                            try:
                                                call_execution.overall_score = float(
                                                    success_eval
                                                )
                                            except (ValueError, TypeError):
                                                call_execution.overall_score = None
                                    elif isinstance(success_eval, (int, float)):
                                        call_execution.overall_score = float(
                                            success_eval
                                        )
                                    else:
                                        call_execution.overall_score = None
                                else:
                                    call_execution.overall_score = None

                    except Exception as e:
                        logger.exception(
                            "csat_evaluation_failed",
                            call_execution_id=str(call_execution.id),
                            error=str(e),
                        )

                        success_eval = analysis.get("successEvaluation")
                        if success_eval is not None:
                            if isinstance(success_eval, str):
                                # Convert string 'true'/'false' to number
                                if success_eval.lower() == "true":
                                    call_execution.overall_score = 1.0
                                elif success_eval.lower() == "false":
                                    call_execution.overall_score = 0.0
                                else:
                                    # Try to parse as numeric string
                                    try:
                                        call_execution.overall_score = float(
                                            success_eval
                                        )
                                    except (ValueError, TypeError):
                                        call_execution.overall_score = None
                            elif isinstance(success_eval, (int, float)):
                                call_execution.overall_score = float(success_eval)
                            else:
                                call_execution.overall_score = None
                        else:
                            call_execution.overall_score = None

                    call_execution.call_summary = call_data.summary

                # Store message information
                if call_data.transcript:
                    messages = call_data.transcript.get("transcripts", [])
                    call_execution.message_count = len(messages)

                    # Calculate response time from messages
                    total_response_time = 0
                    response_count = 0

                    for msg in messages:
                        if msg.get("role") == "bot" and msg.get("duration"):
                            total_response_time += msg["duration"]
                            response_count += 1

                    if response_count > 0:
                        call_execution.response_time_ms = int(
                            total_response_time / response_count
                        )

                    # Calculate conversation metrics
                    try:
                        metrics_calculator = ConversationMetricsCalculator()
                        conversation_metrics = metrics_calculator.calculate_metrics(
                            call_data.raw_log.get(self.system_voice_provider, {}),
                            is_outbound=is_outbound,
                        )

                        # Store conversation metrics
                        call_execution.avg_agent_latency_ms = (
                            conversation_metrics.avg_agent_latency_ms
                        )
                        call_execution.user_interruption_count = (
                            conversation_metrics.user_interruption_count
                        )
                        call_execution.user_interruption_rate = (
                            conversation_metrics.user_interruption_rate
                        )
                        call_execution.user_wpm = conversation_metrics.user_wpm
                        call_execution.bot_wpm = conversation_metrics.bot_wpm
                        call_execution.talk_ratio = conversation_metrics.talk_ratio
                        call_execution.ai_interruption_count = (
                            conversation_metrics.ai_interruption_count
                        )
                        call_execution.ai_interruption_rate = (
                            conversation_metrics.ai_interruption_rate
                        )
                        call_execution.avg_stop_time_after_interruption_ms = (
                            conversation_metrics.avg_stop_time_after_interruption_ms
                        )
                        call_execution.conversation_metrics_data = (
                            conversation_metrics.detailed_data
                        )

                    except Exception as e:
                        logger.warning(
                            f"Failed to calculate conversation metrics for call {call_execution.service_provider_call_id}: {str(e)}"
                        )
                        # Continue without metrics if calculation fails

                # Store metadata if available
                if call_data.metadata:
                    # Merge with existing metadata
                    existing_metadata = call_execution.call_metadata or {}
                    existing_metadata |= call_data.metadata.get(provider_key, {})
                    call_execution.call_metadata = (
                        existing_metadata | call_data.metadata
                    )

                call_execution.transcript_available = call_data.transcript_available
                call_execution.duration_seconds = int(call_data.duration_seconds)

                # Zero-length calls are considered failed
                if call_execution.duration_seconds == 0:
                    call_execution.status = CallExecution.CallStatus.FAILED

                # List all fields that may have been modified
                call_execution.provider_call_data.get(self.system_voice_provider.value)[
                    "call_data_stored"
                ] = True
                fields_to_update = [
                    "assistant_id",
                    "customer_number",
                    "call_type",
                    "started_at",
                    "ended_at",
                    "completed_at",
                    "duration_seconds",
                    "recording_available",
                    "recording_url",
                    "call_summary",
                    "ended_reason",
                    "cost_cents",
                    "stt_cost_cents",
                    "llm_cost_cents",
                    "tts_cost_cents",
                    "vapi_cost_cents",
                    "analysis_data",
                    "overall_score",
                    "evaluation_data",
                    "message_count",
                    "response_time_ms",
                    "avg_agent_latency_ms",
                    "user_interruption_count",
                    "user_interruption_rate",
                    "user_wpm",
                    "bot_wpm",
                    "talk_ratio",
                    "ai_interruption_count",
                    "ai_interruption_rate",
                    "avg_stop_time_after_interruption_ms",
                    "conversation_metrics_data",
                    "transcript_available",
                    "status",
                    "provider_call_data",
                    "call_metadata",  # JSONFields that may be modified in-place
                ]
                call_execution.save(update_fields=fields_to_update)
                # Calculate call duration and deduct cost if call is completed and has recording
                if (
                    call_execution.status == CallExecution.CallStatus.COMPLETED
                    and call_execution.recording_url
                ):
                    # Calculate duration from started_at and completed_at if not already set
                    if (
                        not call_execution.duration_seconds
                        and call_execution.started_at
                        and call_execution.completed_at
                    ):
                        duration_timedelta = (
                            call_execution.completed_at - call_execution.started_at
                        )
                        call_execution.duration_seconds = int(
                            duration_timedelta.total_seconds()
                        )
                        call_execution.save(update_fields=["duration_seconds"])

                    # Deduct cost based on call duration ($0.25 per minute)
                    if call_execution.duration_seconds:
                        close_old_connections()
                        self._deduct_call_cost(call_execution)

                logger.info(f"Stored complete call data for {call_execution.id}")
            except Exception as e:
                logger.error(f"Error storing call data: {str(e)}")
                traceback.print_exc()
            close_old_connections()
            # Store transcript data
            self._fetch_and_store_transcript(call_execution_id)
            # call_execution.refresh_from_db()
            # self._get_call_transcript_data(call_execution,url_save_only=True)

            # Cleanup for outbound calls: delete simulation assistant and release phone
            if is_outbound:
                self._cleanup_outbound_call_resources(call_execution)

        except Exception as e:
            logger.error(
                f"Error storing complete call data for call {call_execution_id}: {str(e)}"
            )
            traceback.print_exc()

    def _store_customer_call_artifacts(
        self,
        call_execution: CallExecution,
        customer_call_id: str | None,
        call_data: FAGICallData,
    ) -> None:
        """
        Persist customer-provided call information including costs, latency metrics,
        and logs into the CallExecution model.
        """
        try:
            if not call_data:
                return

            update_fields: set[str] = set()
            agent_version = (
                call_execution.agent_version
                or call_execution.test_execution.agent_definition.latest_version
            )
            snapshot = agent_version.configuration_snapshot or {}
            provider = snapshot.get("provider", ProviderChoices.VAPI)
            log_url = call_data.log_url

            if customer_call_id:
                call_execution.customer_call_id = customer_call_id
                update_fields.add("customer_call_id")

            if log_url:
                call_execution.customer_log_url = log_url
                update_fields.add("customer_log_url")
                from ee.voice.tasks.call_log_tasks import ingest_call_logs_task

                ingest_call_logs_task.apply_async(
                    args=(str(call_execution.id), log_url),
                    kwargs={
                        "verify_ssl": False,
                        "source": CallLogEntry.LogSource.CUSTOMER,
                    },
                )

            performance_metrics = call_data.performance_metrics.get(provider, {})
            customer_metrics_result = self.voice_service_manager.get_customer_metrics(
                call_data
            )
            normalized_metrics = customer_metrics_result.system_metrics
            cost_breakdown = customer_metrics_result.cost_breakdown
            total_cost = customer_metrics_result.total_cost
            if normalized_metrics:
                if provider == ProviderChoices.VAPI:
                    call_execution.customer_latency_metrics = {
                        "systemMetrics": normalized_metrics,
                        "turnLatencies": performance_metrics.get("turnLatencies", []),
                    }
                update_fields.add("customer_latency_metrics")

            if cost_breakdown:
                call_execution.customer_cost_breakdown = cost_breakdown
                update_fields.add("customer_cost_breakdown")

            if total_cost == 0 and call_data.cost is not None:
                try:
                    total_cost = float(call_data.cost)
                except (TypeError, ValueError):
                    total_cost = 0.0

            if total_cost:
                call_execution.customer_cost_cents = int(round(total_cost * 100))
                update_fields.add("customer_cost_cents")

            if update_fields:
                call_execution.save(update_fields=list(update_fields))
        except Exception as e:
            logger.exception(f"Error storing customer call artifacts.{e}")

    @staticmethod
    def _deduct_call_cost(call_execution: CallExecution):
        """
        Deduct cost for completed voice call based on duration

        Args:
            call_execution: CallExecution object with duration information
        """
        try:
            # Get organization from the call execution
            organization = call_execution.test_execution.run_test.organization

            if call_execution.simulation_call_type == SimulationCallType.TEXT:
                api_call_type_instance, created = APICallType.objects.get_or_create(
                    name="text_call",
                    defaults={
                        "description": "Text call charges based on number of turns"
                    },
                )

                no_of_fagi_agent_turns = ChatMessageModel.objects.filter(
                    call_execution=call_execution,
                    role=ChatMessageModel.RoleChoices.USER,
                ).count()
                total_tokens = (
                    ChatMessageModel.objects.filter(
                        call_execution=call_execution
                    ).aggregate(total=Sum("tokens"))["total"]
                    or 0
                )
                config = {
                    "call_execution_id": str(call_execution.id),
                    "no_of_agent_turns": no_of_fagi_agent_turns,
                    "total_tokens": total_tokens,
                    "default_value": "0.005",
                    "reference_id": str(call_execution.id),
                    "source": "text_call",
                }
                deduct_cost_for_request(
                    organization=organization,
                    api_call_type="text_call",
                    api_call_type_instance=api_call_type_instance,
                    input_tokens=1,  # Not applicable for text calls
                    config=config,
                    source="text_call",
                    source_id=str(call_execution.id),
                    workspace=call_execution.test_execution.run_test.workspace,
                )
                logger.info(
                    f"Successfully deducted cost for chat call {call_execution.id}"
                )

                # Dual-write: emit usage event for text sim
                try:
                    try:
                        from ee.usage.schemas.event_types import BillingEventType
                    except ImportError:
                        BillingEventType = None
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

                    emit(
                        UsageEvent(
                            org_id=str(organization.id),
                            event_type=BillingEventType.TEXT_CALL,
                            amount=total_tokens,
                            properties={
                                "source": "simulate",
                                "source_id": str(call_execution.id),
                                "turns": no_of_fagi_agent_turns,
                                "total_tokens": total_tokens,
                            },
                        )
                    )
                except Exception:
                    pass

                return

            # Calculate cost: $0.25 per minute
            cost_per_minute = Decimal("0.25")
            duration_minutes = Decimal(str(call_execution.duration_seconds)) / Decimal(
                "60"
            )
            total_cost = duration_minutes * cost_per_minute

            logger.info(
                f"Calculating cost for call {call_execution.id}: {duration_minutes:.2f} minutes = ${total_cost:.2f}"
            )

            # Get or create API call type for voice calls
            api_call_type_instance, created = APICallType.objects.get_or_create(
                name="voice_call",
                defaults={"description": "Voice call charges based on duration"},
            )

            # Create config for the cost deduction
            config = {
                "call_execution_id": str(call_execution.id),
                "service_provider_call_id": call_execution.service_provider_call_id,
                "duration_seconds": call_execution.duration_seconds,
                "cost_per_minute": str(cost_per_minute),
                "duration_minutes": str(duration_minutes),
                "recording_url": call_execution.recording_url
                or call_execution.stereo_recording_url,
                "phone_number": call_execution.phone_number,
                "reference_id": str(call_execution.id),
                "source": "voice_call",
            }

            # Deduct cost using the existing cost deduction system
            deduct_cost_for_request(
                organization=organization,
                api_call_type="voice_call",
                api_call_type_instance=api_call_type_instance,
                input_tokens=1,  # Not applicable for voice calls
                config=config,
                source="voice_call",
                source_id=str(call_execution.id),
                workspace=call_execution.test_execution.run_test.workspace,
            )

            logger.info(f"Successfully deducted cost for call {call_execution.id}")

            # Dual-write: emit usage event for new billing system
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                emit(
                    UsageEvent(
                        org_id=str(organization.id),
                        event_type=BillingEventType.VOICE_CALL,
                        amount=max(1, round(float(duration_minutes))),
                        properties={
                            "source": "simulate",
                            "source_id": str(call_execution.id),
                            "duration_seconds": call_execution.duration_seconds,
                            "provider": call_execution.service_provider_call_id or "",
                        },
                    )
                )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error deducting call cost for {call_execution.id}: {str(e)}")
            traceback.print_exc()
            # Don't raise - cleanup failures shouldn't break the call flow

    def _cleanup_outbound_call_resources(self, call_execution):
        """
        Cleanup resources used for outbound calls:
        1. Delete simulation assistant from the service provider
        2. Release phone number back to pool

        Args:
            call_execution: CallExecution instance
        """
        try:
            logger.info(f"Starting cleanup for outbound call {call_execution.id}")

            # Step 1: Delete simulation assistant
            simulation_assistant_id = call_execution.call_metadata.get(
                "simulation_assistant_id"
            )

            if simulation_assistant_id:
                try:
                    # Delete the assistant using system voice provider manager (provider-specific logic lives there)
                    self.voice_service_manager.engine.delete_assistant(
                        simulation_assistant_id
                    )

                    logger.info(
                        f"Deleted simulation assistant {simulation_assistant_id}"
                    )

                    # Mark as cleaned up in metadata
                    if not call_execution.call_metadata:
                        call_execution.call_metadata = {}
                    call_execution.call_metadata["simulation_assistant_deleted"] = True
                    call_execution.save(update_fields=["call_metadata"])

                except Exception as e:
                    logger.error(
                        f"Failed to delete simulation assistant {simulation_assistant_id}: {str(e)}"
                    )
                    # Don't fail the entire cleanup if assistant deletion fails

            # Step 2: Release phone number back to pool
            simulation_phone_id = call_execution.call_metadata.get(
                "simulation_phone_id"
            )

            if simulation_phone_id:
                try:
                    released = PhoneNumberService.release_phone_number(
                        simulation_phone_id
                    )

                    if released:
                        logger.info(
                            f"Released phone number {call_execution.phone_number} back to pool"
                        )

                        # Mark as released in metadata
                        if not call_execution.call_metadata:
                            call_execution.call_metadata = {}
                        call_execution.call_metadata["phone_released"] = True
                        call_execution.save(update_fields=["call_metadata"])
                    else:
                        logger.warning(
                            f"Failed to release phone number {simulation_phone_id}"
                        )

                except Exception as e:
                    logger.error(
                        f"Error releasing phone number {simulation_phone_id}: {str(e)}"
                    )
                    # Don't fail the entire cleanup if phone release fails

            logger.info(f"Cleanup completed for outbound call {call_execution.id}")

        except Exception as e:
            logger.error(
                f"Error in outbound call cleanup for {call_execution.id}: {str(e)}"
            )
            traceback.print_exc()
            # Don't raise - cleanup failures shouldn't break the call flow

    def _check_and_update_eval_completion(
        self,
        call_execution,
        eval_config_ids=None,
        run_test=None,
        skip_status_update=False,
    ):
        """
        Check if all expected eval configs for a call_execution have completed,
        and update eval_completed flag and test_execution status accordingly.

        This method handles async eval configs by checking if all expected configs
        have results (not "pending" status) before marking as completed.

        Args:
            call_execution: CallExecution instance
            eval_config_ids: Optional list of specific eval config IDs that should be completed.
                           If None, checks all configs for the run_test
            run_test: Optional RunTest instance. If None, gets from call_execution
            skip_status_update: If True, do not transition status to COMPLETED. Used when
                the caller (e.g. Temporal workflow) manages the status transition itself.
        """
        try:
            # Refresh from DB to get latest eval_outputs (important for async operations)
            call_execution.refresh_from_db()

            if not run_test:
                run_test = call_execution.test_execution.run_test

            # Get expected eval configs - either specific ones or all for the run test
            if eval_config_ids:
                expected_eval_configs = SimulateEvalConfig.objects.filter(
                    id__in=eval_config_ids, deleted=False
                )
            else:
                expected_eval_configs = SimulateEvalConfig.objects.filter(
                    run_test=run_test, deleted=False
                )

            if not expected_eval_configs.exists():
                # No eval configs expected, mark as completed (provider-agnostic internal flag)
                if not call_execution.call_metadata:
                    call_execution.call_metadata = {}
                call_execution.call_metadata["eval_completed"] = True
                # Also mark call as COMPLETED if it's still in ANALYZING state
                update_fields = ["call_metadata"]
                if (
                    not skip_status_update
                    and call_execution.status == CallExecution.CallStatus.ANALYZING
                ):
                    call_execution.status = CallExecution.CallStatus.COMPLETED
                    update_fields.append("status")
                call_execution.save(update_fields=update_fields)
                if not skip_status_update:
                    self._check_and_update_test_execution_completion(
                        call_execution.test_execution_id
                    )
                return

            # Check if all expected eval configs have completed results
            # A config is considered completed if:
            # 1. It has an entry in eval_outputs
            # 2. The entry doesn't have status="pending"
            eval_outputs = call_execution.eval_outputs or {}
            all_configs_completed = True

            for eval_config in expected_eval_configs:
                eval_config_id_str = str(eval_config.id)
                eval_result = eval_outputs.get(eval_config_id_str)

                if not eval_result:
                    # No result yet for this config
                    all_configs_completed = False
                    break

                # Check if status is "pending" (means it's still running async)
                if eval_result.get("status") == "pending":
                    all_configs_completed = False
                    break

            # Only update eval_completed if all configs are done and it's not already set
            if all_configs_completed:
                if not call_execution.call_metadata:
                    call_execution.call_metadata = {}

                # Use select_for_update within a transaction to prevent race conditions
                # when multiple async tasks complete simultaneously
                try:
                    with transaction.atomic():
                        # Refresh again with select_for_update to get latest state
                        call_execution_locked = (
                            CallExecution.objects.select_for_update().get(
                                id=call_execution.id
                            )
                        )

                        # Double-check eval_completed is not already True (another task might have set it)
                        if not call_execution_locked.call_metadata.get(
                            "eval_completed", False
                        ):
                            # Re-check completion after acquiring lock (another task might have updated eval_outputs)
                            eval_outputs_locked = (
                                call_execution_locked.eval_outputs or {}
                            )
                            all_configs_still_completed = True

                            for eval_config in expected_eval_configs:
                                eval_config_id_str = str(eval_config.id)
                                eval_result = eval_outputs_locked.get(
                                    eval_config_id_str
                                )

                                if (
                                    not eval_result
                                    or eval_result.get("status") == "pending"
                                ):
                                    all_configs_still_completed = False
                                    break

                            if all_configs_still_completed:
                                call_execution_locked.call_metadata[
                                    "eval_completed"
                                ] = True
                                # Also mark call as COMPLETED if it's still in ANALYZING state
                                update_fields = ["call_metadata"]
                                if (
                                    not skip_status_update
                                    and call_execution_locked.status
                                    == CallExecution.CallStatus.ANALYZING
                                ):
                                    call_execution_locked.status = (
                                        CallExecution.CallStatus.COMPLETED
                                    )
                                    update_fields.append("status")
                                call_execution_locked.save(update_fields=update_fields)
                                logger.info(
                                    f"All evaluations completed for call {call_execution_locked.id} "
                                    f"({len(expected_eval_configs)} eval configs)"
                                )

                                if not skip_status_update:
                                    # Check if all calls in this test_execution have eval_completed = True
                                    self._check_and_update_test_execution_completion(
                                        call_execution_locked.test_execution_id
                                    )
                except Exception as e:
                    # If select_for_update fails (e.g., outside transaction), fall back to regular update
                    # This can happen in some Celery task contexts
                    logger.warning(
                        f"Could not use select_for_update for call {call_execution.id}, "
                        f"falling back to regular update: {str(e)}"
                    )
                    # Refresh and check one more time before updating
                    call_execution.refresh_from_db()
                    if not call_execution.call_metadata.get("eval_completed", False):
                        # Quick check if still all completed
                        eval_outputs_final = call_execution.eval_outputs or {}
                        all_still_completed = all(
                            eval_outputs_final.get(str(ec.id), {}).get("status")
                            != "pending"
                            and eval_outputs_final.get(str(ec.id)) is not None
                            for ec in expected_eval_configs
                        )
                        if all_still_completed:
                            call_execution.call_metadata["eval_completed"] = True
                            # Also mark call as COMPLETED if it's still in ANALYZING state
                            update_fields = ["call_metadata"]
                            if (
                                not skip_status_update
                                and call_execution.status
                                == CallExecution.CallStatus.ANALYZING
                            ):
                                call_execution.status = (
                                    CallExecution.CallStatus.COMPLETED
                                )
                                update_fields.append("status")
                            call_execution.save(update_fields=update_fields)
                            logger.info(
                                f"All evaluations completed for call {call_execution.id} "
                                f"({len(expected_eval_configs)} eval configs) - fallback path"
                            )
                            if not skip_status_update:
                                self._check_and_update_test_execution_completion(
                                    call_execution.test_execution_id
                                )
            else:
                logger.debug(
                    f"Not all eval configs completed for call {call_execution.id}. "
                    f"Expected {len(expected_eval_configs)} configs, "
                    f"found {len([k for k, v in eval_outputs.items() if v.get('status') != 'pending'])} completed"
                )

        except Exception as e:
            logger.error(
                f"Error checking eval completion for call {call_execution.id}: {str(e)}"
            )
            traceback.print_exc()

    def _check_and_update_test_execution_completion(self, test_execution_id):
        """
        Check if all call executions in a test_execution have eval_completed = True,
        and update test_execution status to COMPLETED if so.

        Args:
            test_execution_id: TestExecution ID to check
        """
        try:
            all_calls_completed = (
                not CallExecution.objects.filter(
                    test_execution_id=test_execution_id, deleted=False
                )
                .filter(
                    Q(call_metadata__isnull=True)
                    | Q(call_metadata__eval_completed__isnull=True)
                    | Q(call_metadata__eval_completed=False)
                )
                .exists()
            )

            if all_calls_completed:
                # Update test_execution status
                updated = TestExecution.objects.filter(id=test_execution_id).update(
                    status=TestExecution.ExecutionStatus.COMPLETED
                )
                if updated:
                    logger.info(
                        f"Test execution {test_execution_id} marked as completed - all evaluations done"
                    )
        except Exception as e:
            logger.error(
                f"Error checking test execution completion for {test_execution_id}: {str(e)}"
            )
            traceback.print_exc()

    def _run_simulate_evaluations(
        self,
        call_execution,
        eval_config_ids=None,
        skip_existing=False,
        skip_status_update=False,
    ):
        """
        Run evaluations from SimulateEvalConfig for a completed call execution

        Args:
            call_execution: CallExecution instance
            eval_config_ids: Optional list of specific eval config IDs to run. If None, runs all configs for the run_test
            skip_existing: If True, skip evaluations that already exist for this call execution
            skip_status_update: If True, do not transition status to COMPLETED. Used when
                the caller (e.g. Temporal workflow) manages the status transition itself.
        """
        try:
            close_old_connections()
            # Get the test execution and run test
            run_test = call_execution.test_execution.run_test

            # Refresh from DB to get latest data (including provider_call_data from _store_complete_call_data)
            call_execution.refresh_from_db()

            # Mark evaluations as started
            if not call_execution.call_metadata:
                call_execution.call_metadata = {}
            call_execution.call_metadata["eval_started"] = True
            call_execution.save(update_fields=["call_metadata"])
            logger.info(f"Starting evaluations for call {call_execution.id}")

            # Get eval configs - either specific ones or all for the run test
            if eval_config_ids:
                eval_configs = SimulateEvalConfig.objects.filter(
                    id__in=eval_config_ids, deleted=False
                )
            else:
                eval_configs = SimulateEvalConfig.objects.filter(
                    run_test=run_test, deleted=False
                )

            if not eval_configs.exists():
                logger.info(f"No evaluation configs found for run test {run_test.id}")
                if not call_execution.call_metadata:
                    call_execution.call_metadata = {}
                call_execution.call_metadata["eval_completed"] = True
                update_fields = ["call_metadata"]
                if (
                    not skip_status_update
                    and call_execution.status == CallExecution.CallStatus.ANALYZING
                ):
                    call_execution.status = CallExecution.CallStatus.COMPLETED
                    update_fields.append("status")
                call_execution.save(update_fields=update_fields)
                if not skip_status_update:
                    self._check_and_update_test_execution_completion(
                        call_execution.test_execution_id
                    )
                return

            message_count, has_agent_message, has_customer_message = (
                self._get_conversation_presence_signals(call_execution)
            )
            skip_decision = decide_processing_skip(
                message_count=message_count,
                has_agent_message=has_agent_message,
                has_customer_message=has_customer_message,
                duration_seconds=call_execution.duration_seconds,
            )
            if skip_decision.processing_skipped:
                logger.info(
                    f"Skipping evaluations for call execution {call_execution.id}: "
                    f"{skip_decision.processing_skip_reason}"
                )
                self._mark_processing_skipped_for_eval_rerun(
                    call_execution=call_execution,
                    eval_configs=eval_configs,
                    reason=skip_decision.processing_skip_reason,
                    skip_status_update=skip_status_update,
                )
                return

            # Get call transcript data
            transcript_data = self._get_call_transcript_data(call_execution)

            # Check if we have transcript data and voice recording link
            if not transcript_data["transcript"]:
                logger.info(
                    f"No transcript data available for call execution {call_execution.id}, skipping evaluations"
                )
                self._mark_processing_skipped_for_eval_rerun(
                    call_execution=call_execution,
                    eval_configs=eval_configs,
                    reason="Call transcript is unavailable, so processing was skipped.",
                    skip_status_update=skip_status_update,
                )
                return

            # Fetch the TestExecution once before the eval loop. The call
            # execution was loaded with ``select_related("test_execution")``
            # so we reuse that instance and refresh only the status column
            # to pick up any CANCELLED/CANCELLING flip.
            test_execution = call_execution.test_execution
            test_execution.refresh_from_db(fields=["status"])

            # Run each evaluation
            for eval_config in eval_configs:
                try:
                    # # Skip if evaluation already exists and skip_existing is True
                    # if skip_existing and call_execution.eval_outputs and str(eval_config.id) in call_execution.eval_outputs:
                    #     logger.info(
                    #         f"Skipping evaluation {eval_config.id} for call {call_execution.id} - already exists"
                    #     )
                    #     continue

                    # Log if we're overwriting an existing evaluation
                    if (
                        call_execution.eval_outputs
                        and str(eval_config.id) in call_execution.eval_outputs
                    ):
                        logger.info(
                            f"Evaluation {eval_config.id} already exists for call {call_execution.id}, "
                            f"it will be overwritten"
                        )

                    if test_execution.status not in [
                        TestExecution.ExecutionStatus.CANCELLED,
                        TestExecution.ExecutionStatus.CANCELLING,
                    ]:
                        self._run_single_simulate_evaluation(
                            eval_config, call_execution, transcript_data
                        )
                        logger.info(
                            f"Successfully ran evaluation {eval_config.name} ({eval_config.id}) "
                            f"on call execution {call_execution.id}"
                        )
                except Exception as e:
                    logger.error(f"Error running evaluation {eval_config.id}: {str(e)}")
                    traceback.print_exc()

            # Run tool evaluation before marking as completed (only if enabled)
            if test_execution.run_test.enable_tool_evaluation:
                try:
                    self._run_tool_evaluation(call_execution, test_execution)
                except Exception as e:
                    logger.error(
                        f"Error running tool evaluation for call {call_execution.id}: {str(e)}"
                    )
                    traceback.print_exc()
            else:
                logger.info(
                    f"Tool evaluation disabled for run test {test_execution.run_test.id}, skipping"
                )

            # Check if all eval configs are completed before marking as done
            # This handles the case where eval configs might be running asynchronously
            self._check_and_update_eval_completion(
                call_execution,
                eval_config_ids=eval_config_ids,
                run_test=run_test,
                skip_status_update=skip_status_update,
            )

        except Exception as e:
            # On error, still try to check completion in case some configs completed
            try:
                self._check_and_update_eval_completion(
                    call_execution,
                    eval_config_ids=eval_config_ids,
                    skip_status_update=skip_status_update,
                )
            except Exception:
                pass
            logger.error(f"Error in _run_simulate_evaluations: {str(e)}")
            traceback.print_exc()

    def _mark_processing_skipped_for_eval_rerun(
        self,
        call_execution: CallExecution,
        eval_configs,
        reason: str,
        skip_status_update: bool,
    ) -> None:
        """Persist skipped processing outcomes for eval-only reruns."""
        call_execution.call_metadata = call_execution.call_metadata or {}

        call_execution.call_metadata = set_processing_skip_metadata(
            call_execution.call_metadata,
            skipped=True,
            reason=reason,
        )
        call_execution.call_metadata["eval_started"] = True
        call_execution.call_metadata["eval_completed"] = True

        if not call_execution.eval_outputs:
            call_execution.eval_outputs = {}

        for eval_config in eval_configs:
            call_execution.eval_outputs[str(eval_config.id)] = (
                build_skipped_eval_output_payload(
                    eval_name=eval_config.name,
                    reason=reason,
                )
            )

        update_fields = ["call_metadata", "eval_outputs"]
        if (
            not skip_status_update
            and call_execution.status == CallExecution.CallStatus.ANALYZING
        ):
            call_execution.status = CallExecution.CallStatus.COMPLETED
            update_fields.append("status")

        call_execution.save(update_fields=update_fields)

        if not skip_status_update:
            self._check_and_update_test_execution_completion(
                call_execution.test_execution_id
            )

    def _get_conversation_presence_signals(
        self,
        call_execution: CallExecution,
    ) -> tuple[int, bool, bool]:
        """Collect transcript coverage data for skip decision logic."""
        message_count = 0
        has_agent_message = False
        has_customer_message = False

        call_metadata = call_execution.call_metadata or {}
        call_direction = str(call_metadata.get("call_direction") or "").strip().lower()

        call_type_lower = str(call_execution.call_type or "").strip().lower()
        is_outbound = call_direction == "outbound"
        if call_direction not in {"inbound", "outbound"}:
            is_outbound = (
                "outbound" in call_type_lower and "inbound" not in call_type_lower
            )

        from ee.voice.utils.transcript_roles import SpeakerRoleResolver

        provider = SpeakerRoleResolver.detect_provider(
            call_execution.provider_call_data
        )
        agent_roles, customer_roles = SpeakerRoleResolver.get_skip_decision_role_sets(
            provider=provider,
            is_outbound=is_outbound,
        )

        if call_execution.simulation_call_type == CallExecution.SimulationCallType.TEXT:
            for chat_message in call_execution.chat_messages.all().order_by(
                "created_at"
            ):
                role_lower = str(getattr(chat_message, "role", "") or "").lower()
                messages = getattr(chat_message, "messages", None) or []
                for message in messages:
                    message_count += 1
                    text = str(message or "")
                    has_content = bool(text.strip())
                    if has_content and role_lower in agent_roles:
                        has_agent_message = True
                    if has_content and role_lower in customer_roles:
                        has_customer_message = True
        else:
            for role, content in call_execution.transcripts.values_list(
                "speaker_role", "content"
            ):
                message_count += 1
                role_lower = str(role or "").lower()
                has_content = bool(content and content.strip())
                if has_content and role_lower in agent_roles:
                    has_agent_message = True
                if has_content and role_lower in customer_roles:
                    has_customer_message = True

        return message_count, has_agent_message, has_customer_message

    def _get_call_transcript_data(self, call_execution, url_save_only=False):
        """
        Get transcript and voice recording data from call execution
        Converts audio URLs to S3 URLs and saves them to the database

        Args:
            call_execution: CallExecution instance

        Returns:
            dict: Transcript and voice recording data
        """
        from ee.voice.utils.transcript_roles import SpeakerRoleResolver

        transcript_data = {
            "transcript": "",
            "voice_recording": "",
            "assistant_recording": "",
            "customer_recording": "",
            "stereo_recording": "",
            "user_chat_transcript": "",
            "assistant_chat_transcript": "",
        }

        try:
            if not url_save_only:
                # Get transcript from CallTranscript model
                if (
                    call_execution.simulation_call_type
                    == CallExecution.SimulationCallType.TEXT
                ):
                    transcripts = call_execution.chat_messages.all().order_by(
                        "created_at"
                    )
                else:
                    transcripts = call_execution.transcripts.all().order_by(
                        "start_time_ms"
                    )

                if transcripts.exists():
                    transcript_text = []
                    user_chat_transcript_text = []
                    assistant_chat_transcript_text = []

                    # Add context information at the beginning for evaluation agent
                    context_info = []
                    if call_execution.call_metadata.get("agent_description"):
                        context_info.append(
                            f"AGENT PROMPT: {call_execution.call_metadata.get('agent_description')}"
                        )
                    if call_execution.call_metadata.get("dynamic_prompt"):
                        context_info.append(
                            f"SIMULATOR AGENT PROMPT USED: {call_execution.call_metadata.get('dynamic_prompt')}"
                        )
                    if call_execution.call_metadata.get("language"):
                        context_info.append(
                            f"LANGUAGE REQUESTED: {call_execution.call_metadata.get('language')}"
                        )
                    if call_execution.call_metadata.get("initial_message"):
                        context_info.append(
                            f"INITIAL MESSAGE REQUESTED: {call_execution.call_metadata.get('initial_message')}"
                        )

                    if context_info:
                        transcript_text.append("=== CALL CONTEXT ===")
                        transcript_text.extend(context_info)
                        transcript_text.append("=== TRANSCRIPT ===")

                    if (
                        call_execution.simulation_call_type
                        == CallExecution.SimulationCallType.TEXT
                    ):
                        chat_messages_list = list(transcripts)
                        for chat_message in chat_messages_list:
                            if isinstance(chat_message, str):
                                logger.warning(
                                    f"chat_message is a string: {chat_message[:100]}, skipping"
                                )
                                continue
                            if not hasattr(chat_message, "role") or not hasattr(
                                chat_message, "messages"
                            ):
                                logger.warning(
                                    f"Unexpected chat_message type: {type(chat_message)}, skipping"
                                )
                                continue
                            if chat_message.messages:
                                for message in chat_message.messages:
                                    if not isinstance(message, str):
                                        message = str(message)

                                    transcript_text.append(
                                        f"{chat_message.role}: {message}"
                                    )

                                    # Need to make changes here as well.
                                    if chat_message.role == "user":
                                        user_chat_transcript_text.append(message)
                                    elif chat_message.role == "assistant":
                                        assistant_chat_transcript_text.append(message)

                    else:
                        eval_provider = SpeakerRoleResolver.detect_provider(
                            call_execution.provider_call_data
                        )
                        eval_dir = (call_execution.call_metadata or {}).get(
                            "call_direction", ""
                        )
                        eval_is_outbound = str(eval_dir).strip().lower() == "outbound"

                        for transcript in transcripts:
                            if transcript.content.strip():
                                eval_role = SpeakerRoleResolver.get_eval_role_label(
                                    transcript.speaker_role,
                                    provider=eval_provider,
                                    is_outbound=eval_is_outbound,
                                )
                                transcript_text.append(
                                    f"{eval_role}: {transcript.content}"
                                )
                    transcript_data["transcript"] = "\n".join(transcript_text)
                    transcript_data["user_chat_transcript"] = "\n".join(
                        user_chat_transcript_text
                    )
                    transcript_data["assistant_chat_transcript"] = "\n".join(
                        assistant_chat_transcript_text
                    )

            # Track if we need to save the call_execution
            needs_save = False
            fields_to_update = []

            logger.info(
                f"Checking for provider call data in call execution {call_execution.id}"
            )
            # try:
            #     if call_execution.recording_available == True:
            #         recording_url = [call_execution.recording_url]
            #         csat = {
            #             "name": "csat_score",
            #             "description": "Evaluates the Customer Satisfaction (CSAT) score for a call between the customer and the agent.",
            #             "criteria": "Assess the overall satisfaction expressed by the customer during the interaction. Consider explicit statements (e.g., 'thank you, this was helpful', 'this is frustrating') as well as implicit behavioral cues such as tone, cooperation, politeness, engagement, or dissatisfaction. Assign a single CSAT score from 1 to 10, where 1 indicates very dissatisfied and 10 indicates very satisfied. Only use evidence present in the interaction; do not infer beyond what is clearly communicated.",
            #             "choices": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
            #             "multi_choice": False,
            #         }
            #         evaluator = DeterministicEvaluator(multi_choice=csat["multi_choice"], choices=csat["choices"], rule_prompt=csat["criteria"], input=recording_url, input_type=["audio"])
            #         result = evaluator._evaluate()
            #         try:
            #             csat_score = result.get("data",[])[0]
            #             call_execution.overall_score = float(csat_score)

            #         except:
            #
            # except Exception as e:
            #     traceback.print_exc()
            # Provider-specific recording URL extraction is handled inside VoiceServiceManager.
            provider_key = None
            if call_execution.provider_call_data:
                # Prefer system provider key; if missing, fall back to the single key if present.
                system_key = str(self.system_voice_provider.value)
                if system_key in call_execution.provider_call_data:
                    provider_key = system_key
                elif len(call_execution.provider_call_data.keys()) == 1:
                    provider_key = next(iter(call_execution.provider_call_data.keys()))

            provider_payload = (
                call_execution.provider_call_data.get(provider_key)
                if (provider_key and call_execution.provider_call_data)
                else None
            )

            recording_urls = self.voice_service_manager.get_recording_urls(
                provider_payload
            )
            if recording_urls:
                recording_object = {}

                from ee.voice.services.types.voice import PersistAudioInput

                call_id = call_execution.service_provider_call_id

                assistant_url = recording_urls.get("assistant")
                if assistant_url:
                    s3_url = self.voice_service_manager.persist_audio_to_s3(
                        PersistAudioInput(
                            call_id=call_id,
                            audio_url=assistant_url,
                            url_type="assistant_recording",
                        )
                    )
                    transcript_data["assistant_recording"] = s3_url
                    recording_object["assistant"] = s3_url

                customer_url = recording_urls.get("customer")
                if customer_url:
                    s3_url = self.voice_service_manager.persist_audio_to_s3(
                        PersistAudioInput(
                            call_id=call_id,
                            audio_url=customer_url,
                            url_type="customer_recording",
                        )
                    )
                    transcript_data["customer_recording"] = s3_url
                    recording_object["customer"] = s3_url

                stereo_url = recording_urls.get("stereo")
                if stereo_url:
                    s3_url = self.voice_service_manager.persist_audio_to_s3(
                        PersistAudioInput(
                            call_id=call_id,
                            audio_url=stereo_url,
                            url_type="stereo_recording",
                        )
                    )
                    if s3_url != stereo_url:
                        call_execution.stereo_recording_url = s3_url
                        needs_save = True
                        fields_to_update.append("stereo_recording_url")
                    transcript_data["stereo_recording"] = s3_url
                    recording_object["stereo"] = s3_url

                # Convert and save main recording URL (combined)
                combined_url = recording_urls.get("combined")
                if combined_url:
                    s3_url = self.voice_service_manager.persist_audio_to_s3(
                        PersistAudioInput(
                            call_id=call_id,
                            audio_url=call_execution.recording_url,
                            url_type="recording",
                        )
                    )
                    if s3_url != combined_url:
                        call_execution.recording_url = s3_url
                        needs_save = True
                        fields_to_update.append("recording_url")
                    transcript_data["voice_recording"] = s3_url
                    recording_object["combined"] = s3_url

                if recording_object:
                    call_execution.provider_call_data.get(
                        self.system_voice_provider.value
                    )["recording"] = recording_object
                    fields_to_update.append("provider_call_data")
                    needs_save = True

            # Save the call_execution if any URLs were converted
            if needs_save:
                call_execution.save(update_fields=fields_to_update)
                logger.info(
                    f"Updated call execution {call_execution.id} with S3 URLs. Fields: {fields_to_update}"
                )

            # Free memory after all conversions are complete
            gc.collect()

        except Exception as e:
            logger.error(f"Error getting transcript data: {str(e)}")
            traceback.print_exc()
            # Clean up memory on error
            gc.collect()

        return transcript_data

    def _run_single_simulate_evaluation(
        self, eval_config, call_execution: CallExecution, transcript_data
    ):
        """
        Run a single SimulateEvalConfig evaluation

        Args:
            eval_config: SimulateEvalConfig instance
            call_execution: CallExecution instance
            transcript_data: dict with transcript and voice_recording data
        """
        try:
            close_old_connections()

            # Get the evaluation template
            eval_template = eval_config.eval_template

            # Prepare mapping with transcript and voice_recording data
            mapping = eval_config.mapping.copy() if eval_config.mapping else {}

            # Replace mapping values with actual data
            updated_mapping = {}
            # scenario_column_order = (
            #     call_execution.scenario.dataset.column_order
            #     if call_execution.scenario
            #     else []
            # )
            scenario_ids = call_execution.test_execution.scenario_ids
            scenario_column_order_qs = (
                Scenarios.objects.filter(id__in=scenario_ids, deleted=False)
                .select_related("dataset")
                .values_list("dataset__column_order", flat=True)
            )
            scenario_column_order_list = list(
                chain.from_iterable(scenario_column_order_qs)
            )

            # Get agent_version with fallback to latest_version if not set on call_execution
            agent_version = call_execution.agent_version
            if not agent_version:
                agent_def = call_execution.test_execution.run_test.agent_definition
                if agent_def:
                    agent_version = agent_def.latest_version
                    logger.debug(
                        f"Using fallback agent_version (latest_version) for call_execution {call_execution.id}"
                    )

            logger.info(
                f"Eval mapping validation for call_execution {call_execution.id}: "
                f"scenario_ids={scenario_ids}, "
                f"column_count={len(scenario_column_order_list)}, "
                f"mapping={mapping}, "
                f"agent_version_id={agent_version.id if agent_version else None}"
            )

            for key, value in mapping.items():
                logger.debug(
                    f"Processing mapping: key='{key}', value='{value}', value_repr={repr(value)}"
                )
                # Skip empty or null mapping values
                if not value or value == "":
                    logger.debug(f"Skipping empty mapping value for key '{key}'")
                    continue

                if value == "transcript":
                    updated_mapping[key] = transcript_data["transcript"]
                elif value == "voice_recording":
                    updated_mapping[key] = transcript_data["voice_recording"]
                elif value == "assistant_recording":
                    updated_mapping[key] = transcript_data["assistant_recording"]
                elif value == "customer_recording":
                    updated_mapping[key] = transcript_data["customer_recording"]
                elif value == "stereo_recording":
                    updated_mapping[key] = transcript_data["stereo_recording"]
                elif value == "user_chat_transcript":
                    updated_mapping[key] = transcript_data["user_chat_transcript"]
                elif value == "assistant_chat_transcript":
                    updated_mapping[key] = transcript_data["assistant_chat_transcript"]
                else:
                    if value == "agent_prompt":
                        if agent_version and agent_version.configuration_snapshot:
                            snapshot = agent_version.configuration_snapshot
                            updated_mapping[key] = snapshot.get("description", "")
                        else:
                            logger.warning(
                                f"agent_version or configuration_snapshot is None for call_execution {call_execution.id}, "
                                f"using empty string for agent_prompt"
                            )
                            updated_mapping[key] = ""
                    # Keep original value if not a special mapping
                    elif value in scenario_column_order_list:
                        metadata = call_execution.call_metadata
                        row_id = metadata.get("row_id")
                        if not row_id:
                            logger.warning(
                                f"row_id not found in call_metadata for call_execution {call_execution.id}, "
                                f"using empty string for mapping key '{key}'"
                            )
                            updated_mapping[key] = ""
                            continue
                        try:
                            cell = Cell.objects.get(
                                row=row_id, column=value, deleted=False
                            )
                            updated_mapping[key] = cell.value
                        except Cell.DoesNotExist:
                            logger.warning(
                                f"Cell not found for row_id={row_id}, column={value} "
                                f"in call_execution {call_execution.id}, using empty string"
                            )
                            updated_mapping[key] = ""
                    else:
                        # Build informative error message with column, dataset, and scenario details
                        column_name = None
                        column_dataset_name = None
                        column_scenario_name = None
                        try:
                            column = Column.objects.select_related("dataset").get(
                                id=value, deleted=False
                            )
                            column_name = column.name
                            if column.dataset:
                                column_dataset_name = column.dataset.name
                                # Find which scenario uses this dataset
                                column_scenario = Scenarios.objects.get(
                                    dataset=column.dataset
                                )
                                if column_scenario:
                                    column_scenario_name = column_scenario.name
                        except (Column.DoesNotExist, Exception):
                            pass

                        # Get test scenario names for context
                        test_scenarios = list(
                            Scenarios.objects.filter(
                                id__in=scenario_ids, deleted=False
                            ).values_list("name", flat=True)
                        )
                        test_scenarios_str = (
                            ", ".join(test_scenarios)
                            if test_scenarios
                            else "unknown scenarios"
                        )

                        # Build clear error message showing the mismatch
                        if column_name and column_scenario_name:
                            error_message = (
                                f"Column mapping mismatch: The evaluation '{eval_config.name}' uses column '{column_name}' "
                                f"from scenario '{column_scenario_name}', but the test is running with different scenario(s): [{test_scenarios_str}]. "
                                f"Please reconfigure the evaluation to use columns from the test scenarios."
                            )
                        elif column_name and column_dataset_name:
                            error_message = (
                                f"Column mapping mismatch: The evaluation '{eval_config.name}' uses column '{column_name}' "
                                f"from dataset '{column_dataset_name}', which is not part of the test scenario(s): [{test_scenarios_str}]. "
                                f"Please reconfigure the evaluation to use columns from the test scenarios."
                            )
                        else:
                            error_message = (
                                f"Column mapping mismatch: Column '{value}' is not available in the test scenario(s): [{test_scenarios_str}]. "
                                f"Please reconfigure the evaluation '{eval_config.name}' to use valid columns."
                            )

                        logger.warning(
                            f"Error running evaluation {eval_config.id}: Invalid column mapping. "
                            f"Key: '{key}', Value: '{value}', "
                            f"Value type: {type(value).__name__}, "
                            f"Scenario IDs: {scenario_ids}, "
                            f"Column name: {column_name}, "
                            f"Column dataset: {column_dataset_name}, "
                            f"Column scenario: {column_scenario_name}, "
                            f"Test scenarios: {test_scenarios_str}, "
                            f"Available scenario columns ({len(scenario_column_order_list)} total): {scenario_column_order_list[:10]}..."
                        )
                        # Store error result in eval_outputs
                        if not call_execution.eval_outputs:
                            call_execution.eval_outputs = {}

                        error_result = {
                            "reason": error_message,
                            "error": "error",
                            "name": eval_config.name,
                            "timestamp": timezone.now().isoformat(),
                        }
                        call_execution.eval_outputs[str(eval_config.id)] = error_result
                        call_execution.eval_outputs[str(eval_config.id)][
                            "status"
                        ] = StatusType.FAILED.value
                        call_execution.save(update_fields=["eval_outputs"])
                        raise ValueError(error_message)

            # Prepare config
            config = eval_config.config.copy() if eval_config.config else {}
            # Don't add mapping to config - it's passed separately as 'mappings' parameter
            # This prevents circular reference when both config and mappings are merged
            # config['mapping'] = updated_mapping  # REMOVED to avoid circular reference

            # Get organization
            organization = call_execution.test_execution.run_test.organization

            # Run the evaluation
            logger.info(
                f"Running evaluation {eval_config.id} for call {call_execution.id}"
            )

            eval_result = run_eval_func(
                config=config,
                mappings=updated_mapping,
                template=eval_template,
                org=organization,
                model=eval_config.model,
                kb_id=eval_config.kb_id,
                error_localizer=eval_config.error_localizer,
                workspace=call_execution.test_execution.run_test.workspace,
                source="simulate",
            )

            if isinstance(eval_result, str):
                if (
                    "insufficient_credits" in eval_result.lower()
                    or "limit reached" in eval_result.lower()
                ):
                    raise ValueError(eval_result)
                raise ValueError(
                    "Evaluation failed. Please contact Future AGI support."
                )

            # Store evaluation result
            if eval_result:
                # Initialize eval_outputs if it doesn't exist
                if not call_execution.eval_outputs:
                    call_execution.eval_outputs = {}

                # Get evaluation output and reason
                eval_output = eval_result.get("output")
                eval_reason = eval_result.get("reason", "")

                # Store the evaluation result with eval_config.id as key
                call_execution.eval_outputs[str(eval_config.id)] = {
                    "output": eval_output,
                    "reason": eval_reason,
                    "output_type": eval_result.get("output_type"),
                    "name": eval_config.name,
                    "status": StatusType.COMPLETED.value,
                }
                call_execution.save(update_fields=["eval_outputs"])

                # Trigger error localization if enabled
                if eval_config.error_localizer and eval_output is not None:
                    try:
                        # Determine if evaluation failed (assuming boolean or numeric output)
                        eval_failed = False
                        if isinstance(eval_output, bool):
                            eval_failed = not eval_output
                        elif isinstance(eval_output, int | float):
                            # Consider it failed if score is less than 0.5 (assuming 0-1 scale)
                            eval_failed = eval_output < 0.8
                        else:
                            # For string outputs, check if it contains failure indicators
                            eval_failed = True

                        if eval_failed:
                            trigger_error_localization_for_simulate(
                                eval_template=eval_template,
                                call_execution=call_execution,
                                eval_config=eval_config,
                                value=eval_output,
                                mapping=updated_mapping,
                                eval_explanation=eval_reason,
                                log_id=None,  # You can add log_id if available
                            )
                            logger.info(
                                f"Triggered error localization for failed evaluation {eval_config.id}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Error triggering error localization for evaluation {eval_config.id}: {str(e)}"
                        )

                logger.info(f"Successfully completed evaluation {eval_config.id}")
            else:
                logger.info(f"Evaluation {eval_config.id} returned no result")

        except Exception as e:
            logger.error(f"Error running evaluation {eval_config.id}: {str(e)}")

            # Store error result in eval_outputs
            if not call_execution.eval_outputs:
                call_execution.eval_outputs = {}

            error_result = {
                "reason": get_specific_error_message(e),
                "error": "error",
                "name": eval_config.name,
                "timestamp": timezone.now().isoformat(),
            }
            call_execution.eval_outputs[str(eval_config.id)] = error_result
            call_execution.eval_outputs[str(eval_config.id)][
                "status"
            ] = StatusType.FAILED.value
            call_execution.save(update_fields=["eval_outputs"])
            raise

    def _aggregate_tool_columns_to_test_execution(self, test_execution):
        """
        Verify and aggregate all tool evaluation columns from all call executions into test_execution's column_order.
        This is called once before marking the test execution as completed to ensure no columns were lost due to race conditions.

        Key feature: Consolidates columns by NAME (not ID) - if multiple calls created columns with the same name
        but different IDs due to race conditions, we merge them and update all tool_outputs to use the canonical ID.

        Strategy:
        1. Remove all tool_evaluation columns from test_execution.column_order
        2. Collect all tool columns from call_execution.evaluation_data['tool_column_order']
        3. Consolidate by columnName - assign canonical ID (first encountered) for each unique name
        4. Update tool_outputs in ALL call_executions to use canonical IDs
        5. Update tool_column_order in ALL call_executions to use canonical IDs
        6. Append consolidated tool columns to column_order

        Handles edge cases:
        - Some call executions may have empty evaluation_data
        - Different call executions may have different columns
        - Same column name MUST have same ID across all call executions
        """
        try:
            # Get all call executions for this test execution
            call_executions = CallExecution.objects.filter(
                test_execution=test_execution
            )

            # Initialize execution_metadata if not present
            if not test_execution.execution_metadata:
                test_execution.execution_metadata = {}

            # Step 1: Remove all tool_evaluation columns from column_order
            column_order = test_execution.execution_metadata.get("column_order", [])
            non_tool_columns = [
                col for col in column_order if col.get("type") != "tool_evaluation"
            ]

            # Step 2 & 3: Collect and consolidate tool columns by columnName
            # Map: columnName -> (canonical_id, canonical_column_def)
            column_name_to_canonical = {}
            # Map: old_id -> canonical_id (for updating tool_outputs)
            id_mappings = {}

            # Collect all tool columns from all call executions
            for call_execution in call_executions:
                # Skip if no evaluation_data or tool_column_order
                if not call_execution.evaluation_data:
                    continue
                if "tool_column_order" not in call_execution.evaluation_data:
                    continue

                tool_columns = call_execution.evaluation_data.get(
                    "tool_column_order", []
                )
                if not tool_columns:
                    continue

                for col in tool_columns:
                    col_name = col.get("column_name") or col.get("columnName")
                    col_id = col.get("id")

                    # Skip invalid columns
                    if not col_name or not col_id:
                        continue

                    if col_name in column_name_to_canonical:
                        # Column name already exists, map this ID to the canonical one
                        canonical_id, _ = column_name_to_canonical[col_name]
                        if col_id != canonical_id:
                            id_mappings[col_id] = canonical_id
                    else:
                        # First occurrence of this column name - this becomes canonical
                        column_name_to_canonical[col_name] = (col_id, col)

            # Step 4 & 5: Update ALL call_executions to use canonical IDs
            # This ensures same column name = same ID everywhere
            calls_updated = 0
            for call_execution in call_executions:
                updated = False

                # Update tool_outputs (may exist even if evaluation_data doesn't)
                if call_execution.tool_outputs and id_mappings:
                    new_tool_outputs = {}
                    for output_id, output_value in call_execution.tool_outputs.items():
                        # Use canonical ID if mapping exists
                        canonical_id = id_mappings.get(output_id, output_id)
                        new_tool_outputs[canonical_id] = output_value
                        if canonical_id != output_id:
                            updated = True

                    if updated:
                        call_execution.tool_outputs = new_tool_outputs

                # Initialize evaluation_data if needed
                if not call_execution.evaluation_data:
                    call_execution.evaluation_data = {}

                # Update this call's tool_column_order to use canonical IDs (do NOT wipe it).
                existing_tool_columns = call_execution.evaluation_data.get(
                    "tool_column_order", []
                )
                new_tool_columns = []
                if isinstance(existing_tool_columns, list):
                    for col in existing_tool_columns:
                        if not isinstance(col, dict):
                            continue
                        col_name = col.get("column_name") or col.get("columnName")
                        if not col_name:
                            continue
                        # If we know this column name, enforce the canonical ID.
                        if col_name in column_name_to_canonical:
                            canonical_id, canonical_def = column_name_to_canonical[
                                col_name
                            ]
                            # Preserve other fields from the existing col (like visibility), but enforce canonical id.
                            new_tool_columns.append({**col, "id": canonical_id})
                        else:
                            # Unknown column name (shouldn't happen), keep as-is.
                            new_tool_columns.append(col)
                call_execution.evaluation_data["tool_column_order"] = new_tool_columns
                if new_tool_columns != existing_tool_columns:
                    updated = True

                # Save if any updates were made
                if updated:
                    call_execution.save(
                        update_fields=["tool_outputs", "evaluation_data"]
                    )
                    calls_updated += 1

            # Step 6: Append consolidated tool columns to column_order
            # Sort by column name for consistent ordering
            consolidated_tool_columns = [
                col_def for (col_id, col_def) in column_name_to_canonical.values()
            ]
            column_order = non_tool_columns + consolidated_tool_columns

            # Save the updated column_order
            test_execution.execution_metadata["column_order"] = column_order
            test_execution.save(update_fields=["execution_metadata"])

            if id_mappings or column_name_to_canonical:
                logger.info(
                    f"Tool column aggregation completed: "
                    f"Found {len(column_name_to_canonical)} unique columns, "
                    f"consolidated {len(id_mappings)} duplicate IDs, "
                    f"updated {calls_updated} call executions"
                )

        except Exception as e:
            logger.error(
                f"Error aggregating tool columns for test execution {test_execution.id}: {str(e)}"
            )
            traceback.print_exc()

    def _run_tool_evaluation(self, call_execution, test_execution):
        """
        Run tool evaluation for a call execution and update column order.
        This is called automatically as part of _run_simulate_evaluations flow.

        Args:
            call_execution: CallExecution instance
            test_execution: TestExecution instance
        """
        try:
            # Check if tool evaluation is enabled for this run test
            if not test_execution.run_test.enable_tool_evaluation:
                logger.info(
                    f"Tool evaluation disabled for run test {test_execution.run_test.id}, skipping"
                )
                return

            # Skip if no service_provider_call_id
            if (
                call_execution.simulation_call_type
                != CallExecution.SimulationCallType.TEXT
                and not call_execution.service_provider_call_id
            ):
                logger.info(
                    f"Skipping tool evaluation for call {call_execution.id} - no service_provider_call_id"
                )
                return

            logger.info(
                f"Running tool evaluation for call execution {call_execution.id}"
            )

            # Initialize the ToolEvalAgent
            agent = ToolEvalAgent()

            # Get API key and customer info from agent definition for customer call evaluation
            agent_definition = test_execution.run_test.agent_definition
            selected_version = test_execution.agent_version
            agent_version = None
            if not selected_version:
                agent_version = agent_definition.latest_version
            else:
                agent_version = agent_definition.get_version(selected_version.id)

            snapshot = agent_version.configuration_snapshot
            # Check if this is a TEXT (chat) agent
            agent_type = agent_definition.agent_type
            is_text_agent = agent_type == AgentDefinition.AgentTypeChoices.TEXT

            # Handle TEXT (chat) agents differently
            if is_text_agent:
                logger.info("Processing TEXT agent - using chat session data")

                # Get chat data from database (ChatMessageModel)
                try:
                    call_data = agent._get_chat_data_from_database(call_execution)
                except Exception as e:
                    logger.error(f"Error fetching chat data from database: {str(e)}")
                    return

                # Extract tool calls from chat messages
                tool_calls_data = agent._extract_tool_calls(call_data)
            else:
                customer_api_key = (
                    snapshot.get("api_key")
                    if snapshot and snapshot.get("api_key")
                    else None
                )

                customer_assistant_id = (
                    snapshot.get("assistant_id")
                    if snapshot and snapshot.get("assistant_id")
                    else None
                )

                # Handle VOICE agents (existing logic)
                logger.info("Processing VOICE agent - using call data")

                # Get our call data via VoiceServiceManager (provider-specific transport/parsing lives there)
                try:
                    our_call_data = self.voice_service_manager.get_call(
                        call_execution.service_provider_call_id, call_data_stored=True
                    )
                except Exception as e:
                    logger.error(f"Error fetching our call data: {str(e)}")
                    return

                # Determine which call ID to use for tool evaluation
                call_id_to_evaluate = None
                is_outbound = (
                    call_execution.call_metadata.get("call_direction") == "outbound"
                )

                # If customer provided their own VAPI credentials, find their call ID
                if customer_api_key and customer_assistant_id:
                    logger.info(
                        "Customer credentials provided, attempting to find customer's call ID"
                    )
                    if is_outbound:
                        logger.info(
                            f"Outbound call, using our call ID: {call_execution.service_provider_call_id}"
                        )
                        customer_call_id = call_execution.service_provider_call_id
                    else:
                        try:
                            from ee.voice.services.types.voice import (
                                FindClientCallInput,
                            )

                            customer_call_id = (
                                self.voice_service_manager.find_client_call(
                                    FindClientCallInput(
                                        customer_api_key=customer_api_key,
                                        customer_assistant_id=customer_assistant_id,
                                        our_call_data=our_call_data,
                                        customer_voice_service_provider=snapshot.get(
                                            "provider", ProviderChoices.VAPI
                                        ),
                                        time_window_seconds=10,
                                    )
                                )
                            )

                        except Exception as e:
                            logger.error(f"Error finding customer call ID: {str(e)}")
                            return
                    if customer_call_id:
                        logger.info(f"Found customer call ID: {customer_call_id}")
                        call_id_to_evaluate = customer_call_id
                        # Use customer's API key for fetching their call data
                        api_key = customer_api_key
                    else:
                        logger.warning(
                            "Could not find matching customer call ID, using our call ID"
                        )
                        return
                else:
                    # No customer credentials, use our call ID
                    logger.info("No customer credentials provided, using our call ID")
                    return

                # Get call data from VAPI using the appropriate API key
                call_data = agent._get_call_data_from_provider(
                    call_id_to_evaluate, api_key=api_key
                )

                # Extract tool calls from messages
                tool_calls_data = agent._extract_tool_calls(call_data)

            if not tool_calls_data:
                # No tool calls - nothing to evaluate
                # Store empty tool_column_order to track that this call was processed
                if not call_execution.evaluation_data:
                    call_execution.evaluation_data = {}
                call_execution.evaluation_data["tool_column_order"] = []
                call_execution.save(update_fields=["evaluation_data"])
                return

            # Initialize tool_outputs if it doesn't exist
            if not call_execution.tool_outputs:
                call_execution.tool_outputs = {}
            else:
                logger.info(
                    f"Tool outputs already exist for call execution {call_execution.id}, skipping"
                )
                return

            # PHASE 1: Build column_order for this specific call
            # Hybrid approach: Update both test_execution (for immediate UI visibility)
            # AND call_execution.evaluation_data (for safety/aggregation later)

            # Initialize test_execution metadata if needed
            if not test_execution.execution_metadata:
                test_execution.execution_metadata = {}

            # Create a copy of the list to avoid mutating the original reference
            # This ensures Django's JSONField change detection works correctly
            test_column_order = list(
                test_execution.execution_metadata.get("column_order", [])
            )

            call_column_order = []
            tool_eval_ids_map = {}  # Map idx to tool_eval_id for the second phase
            columns_updated = False
            tool_name_counts = (
                {}
            )  # tool_name -> occurrence count (per-call) for stable column naming

            for idx, tool_call in enumerate(tool_calls_data):
                try:
                    tool_name = tool_call.get("tool_name", "Unknown")
                    tool_call_id = tool_call.get("tool_call_id", f"unknown_{idx}")

                    # Create stable column name for this tool call:
                    # Use per-tool occurrence index so columns overlap across call executions even when
                    # overall tool call ordering differs between scenarios.
                    tool_occurrence = tool_name_counts.get(tool_name, 0) + 1
                    tool_name_counts[tool_name] = tool_occurrence
                    column_name = f"{tool_name} #{tool_occurrence}"

                    # Check if column already exists in test_execution
                    existing_col = None
                    tool_eval_id = None
                    for col in test_column_order:
                        if col.get("type") == "tool_evaluation" and (
                            col.get("column_name") == column_name
                            or col.get("columnName") == column_name
                        ):
                            existing_col = col
                            tool_eval_id = col["id"]
                            break

                    # Create new column if it doesn't exist
                    if not existing_col:
                        tool_eval_id = str(uuid4())
                        column_def = {
                            "column_name": column_name,
                            "id": tool_eval_id,
                            "eval_config": {
                                "eval_type_id": "tool_evaluation",
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "tool_index": tool_occurrence,
                                "name": column_name,
                            },
                            "visible": True,
                            "type": "tool_evaluation",
                        }
                        test_column_order.append(column_def)
                        columns_updated = True
                    else:
                        column_def = existing_col

                    # Store in call's column_order (for aggregation/verification later)
                    call_column_order.append(column_def)

                    # Store mapping for second phase
                    tool_eval_ids_map[idx] = {
                        "tool_eval_id": tool_eval_id,
                        "column_name": column_name,
                        "tool_name": tool_name,
                        "tool_call": tool_call,
                    }

                    # Initialize status as "running" in tool_outputs
                    call_execution.tool_outputs[tool_eval_id] = {
                        "value": "",
                        "reason": "",
                        "type": "Pass/Fail",
                        "name": column_name,
                        "error": False,
                        "status": "running",
                    }

                except Exception as e:
                    logger.error(
                        f"Error initializing tool call #{idx + 1} for call {call_execution.id}: {str(e)}"
                    )
                    traceback.print_exc()

            # Update test_execution column_order if new columns were added (for immediate UI visibility)
            if columns_updated:
                test_execution.execution_metadata["column_order"] = test_column_order
                test_execution.save(update_fields=["execution_metadata"])

            # Store this call's column_order in evaluation_data (for verification/aggregation later)
            # Even if empty, we store it to track that this call was processed
            if not call_execution.evaluation_data:
                call_execution.evaluation_data = {}
            call_execution.evaluation_data["tool_column_order"] = call_column_order

            # Save call_execution with tool_outputs and evaluation_data
            call_execution.save(update_fields=["tool_outputs", "evaluation_data"])

            # PHASE 2: Now run the evaluations and update status to "completed" or "error"
            for idx, tool_eval_info in tool_eval_ids_map.items():
                try:
                    tool_eval_id = tool_eval_info["tool_eval_id"]
                    column_name = tool_eval_info["column_name"]
                    tool_name = tool_eval_info["tool_name"]
                    tool_call = tool_eval_info["tool_call"]

                    # Deduct cost for this tool evaluation
                    organization = test_execution.run_test.organization
                    workspace = test_execution.run_test.workspace

                    # Get model details from agent's LLM
                    model = agent.llm.model_name if hasattr(agent, "llm") else None

                    # Create source config for cost tracking
                    source_config = {
                        "source": "simulate_tool_evaluation",
                        "test_execution_id": str(test_execution.id),
                        "call_execution_id": str(call_execution.id),
                        "tool_name": tool_name,
                        "tool_call_id": tool_call.get("tool_call_id", f"unknown_{idx}"),
                        "tool_index": idx + 1,
                    }

                    if model:
                        source_config.update({"model": model})

                    # Determine API call type based on model
                    api_call_type = _get_api_call_type(model=None)
                    # Log and deduct cost
                    api_call_log_row = log_and_deduct_cost_for_api_request(
                        organization=organization,
                        api_call_type=api_call_type,
                        config=source_config,
                        source="simulate_tool_evaluation",
                        source_id=str(test_execution.id),
                        workspace=workspace,
                    )

                    if not api_call_log_row:
                        logger.error(
                            f"API call not allowed for tool evaluation: {tool_name} #{idx + 1}"
                        )
                        call_execution.tool_outputs[tool_eval_id] = {
                            "value": "",
                            "reason": "API call not allowed - cost validation failed",
                            "type": "Pass/Fail",
                            "name": column_name,
                            "error": True,
                            "status": "failed",
                        }
                        continue

                    if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
                        logger.error(
                            f"API call not allowed - status: {api_call_log_row.status}"
                        )
                        call_execution.tool_outputs[tool_eval_id] = {
                            "value": "",
                            "reason": f"API call not allowed - status: {api_call_log_row.status}",
                            "type": "Pass/Fail",
                            "name": column_name,
                            "error": True,
                            "status": "failed",
                        }
                        continue

                    # Now run the evaluation for this specific tool call
                    evaluation = agent._evaluate_single_tool_call(
                        tool_call=tool_call,
                        conversation_context=call_data["conversation_context"],
                        all_tool_calls=tool_calls_data,
                    )

                    # Store result for this individual tool call in tool_outputs with status "completed"
                    call_execution.tool_outputs[tool_eval_id] = {
                        "value": evaluation.get("result", "Failed"),
                        "reason": evaluation.get("summary", ""),
                        "type": "Pass/Fail",
                        "name": column_name,
                        "error": False,
                        "status": "completed",
                    }

                    result_status = (
                        "✓ PASSED" if evaluation.get("result") else "✗ FAILED"
                    )
                    logger.info(
                        f"Successfully evaluated {column_name} for call {call_execution.id}: {result_status}"
                    )

                except Exception as e:
                    logger.error(
                        f"Error evaluating tool call #{idx + 1} for call {call_execution.id}: {str(e)}"
                    )
                    traceback.print_exc()

                    # Store error result for this tool call with status "error"
                    if idx in tool_eval_ids_map:
                        tool_eval_id = tool_eval_ids_map[idx]["tool_eval_id"]
                        tool_name = tool_eval_ids_map[idx]["tool_name"]
                        call_execution.tool_outputs[tool_eval_id] = {
                            "value": "",
                            "reason": get_specific_error_message(str(e)),
                            "type": "Pass/Fail",
                            "name": f"{tool_name} #{idx + 1}",
                            "error": True,
                            "status": "failed",
                        }

            # Save all results in tool_outputs
            call_execution.save(update_fields=["tool_outputs"])
            logger.info(
                f"Successfully completed tool evaluation for call {call_execution.id} - evaluated {len(tool_calls_data)} tool call(s)"
            )

        except Exception as e:
            logger.error(
                f"Error in _run_tool_evaluation for call {call_execution.id}: {str(e)}"
            )
            traceback.print_exc()
            # Error is logged but we don't store partial results - evaluation just skipped for this call


@temporal_activity(
    time_limit=3600,
    max_retries=0,
    retry_delay=300,
    queue="tasks_xl",
)
def _run_simulate_evaluations_task(
    call_execution_id, eval_config_ids=None, skip_existing=False
):
    """
    Temporal activity to run simulate evaluations for a call execution.

    Args:
        call_execution_id: CallExecution ID to run evaluations on
        eval_config_ids: Optional list of specific eval config IDs to run. If None, runs all configs for the run_test
        skip_existing: If True, skip evaluations that already exist for this call execution
    """
    try:
        close_old_connections()
        logger.info(
            f"Running simulate evaluations for call execution {call_execution_id}"
        )
        call_execution = CallExecution.objects.select_related(
            "test_execution",
            "test_execution__run_test",
        ).get(id=call_execution_id)

        test_executor = TestExecutor()
        test_executor._run_simulate_evaluations(
            call_execution, eval_config_ids=eval_config_ids, skip_existing=skip_existing
        )
        return True
    except Exception as e:
        logger.error(
            f"Error running simulate evaluations for call execution {call_execution_id}: {str(e)}"
        )
        traceback.print_exc()
        return False
    finally:
        close_old_connections()


@temporal_activity(
    time_limit=600,  # 10 minutes just to dispatch tasks
    max_retries=0,
    retry_delay=300,
    queue="tasks_xl",
)
def run_new_evals_on_call_executions_task(call_execution_ids, eval_config_ids):
    """
    Temporal activity to dispatch individual evaluation tasks for multiple call executions.
    This task spawns individual tasks for each call execution to enable parallel processing.

    Args:
        call_execution_ids: List of CallExecution IDs to run evaluations on
        eval_config_ids: List of SimulateEvalConfig IDs to run

    Returns:
        dict: Summary of dispatched tasks
    """

    results = {
        "dispatched_tasks": [],
        "total_call_executions": 0,
        "total_eval_configs": len(eval_config_ids),
    }

    try:
        close_old_connections()

        # Validate that call executions exist
        call_executions = CallExecution.objects.filter(id__in=call_execution_ids)

        if not call_executions.exists():
            logger.error(f"No call executions found for IDs: {call_execution_ids}")
            return results

        # Validate that eval configs exist
        eval_configs = SimulateEvalConfig.objects.filter(id__in=eval_config_ids)

        if not eval_configs.exists():
            logger.error(f"No eval configs found for IDs: {eval_config_ids}")
            return results

        # Note: eval_started flag should be updated in bulk before calling this task (in the view)
        # Dispatch individual tasks for each call execution using the unified evaluation task
        for call_execution in call_executions:
            try:
                # Use the unified _run_simulate_evaluations_task with skip_existing=False to overwrite
                task = _run_simulate_evaluations_task.apply_async(
                    args=(str(call_execution.id),),
                    kwargs={"eval_config_ids": eval_config_ids, "skip_existing": False},
                )
                results["dispatched_tasks"].append(
                    {"call_execution_id": str(call_execution.id), "task_id": task.id}
                )
                results["total_call_executions"] += 1

                logger.info(
                    f"Dispatched evaluation task {task.id} for call execution {call_execution.id} "
                    f"with {len(eval_config_ids)} eval configs"
                )

            except Exception as e:
                logger.error(
                    f"Error dispatching task for call execution {call_execution.id}: {str(e)}"
                )
                traceback.print_exc()

        logger.info(
            f"Dispatched {len(results['dispatched_tasks'])} evaluation tasks for "
            f"{results['total_call_executions']} call executions"
        )

        return results

    except Exception as e:
        logger.error(f"Error in run_new_evals_on_call_executions_task: {str(e)}")
        traceback.print_exc()
        return results
    finally:
        close_old_connections()
