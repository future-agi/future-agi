"""
Test execution activities for TestExecutionWorkflow.

Activities for setup, call record creation, and finalization of test executions.
These run on tasks_l queue with moderate timeouts.

IMPORTANT: Each activity calls _close_old_connections() at the start to prevent
connection pool exhaustion when using PgBouncer. Without this, connections
accumulate and hit PgBouncer's pool limit (~20 by default).
"""

import ast
import json
import os
import re
from typing import Any

_UNRESOLVED_TOKEN_RE = re.compile(r"\{\{[^}]+\}\}")

from django.db import close_old_connections
from temporalio import activity

from simulate.temporal.signals import SIGNAL_CANCEL_CALL
from simulate.temporal.types.activities import (
    CancelPendingCallsInput,
    CancelPendingCallsOutput,
    CreateCallRecordsInput,
    CreateCallRecordsOutput,
    FinalizeInput,
    GetUnlaunchedCallsInput,
    GetUnlaunchedCallsOutput,
    SetupTestInput,
    SetupTestOutput,
)


@activity.defn(name="setup_test_execution")
async def setup_test_execution(input: SetupTestInput) -> SetupTestOutput:
    """
    Setup and validate test execution configuration.

    Loads scenarios from database, validates configuration.
    Uses agent_version.configuration_snapshot for all config (not agent_definition).

    Timeout: 2 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Setting up test execution: test_execution_id={input.test_execution_id}, "
            f"scenarios={len(input.scenario_ids)}"
        )

        from simulate.models import Scenarios
        from simulate.models.test_execution import TestExecution

        # Fetch test execution with agent_version
        test_execution = await TestExecution.objects.select_related(
            "run_test__organization",
            "run_test__workspace",
            "agent_version",
            "simulator_agent",
        ).aget(id=input.test_execution_id)

        # Mark as picked up (status remains PENDING until first slot is granted)
        test_execution.picked_up_by_executor = True
        await test_execution.asave(update_fields=["picked_up_by_executor"])

        # Load scenarios
        scenarios_data = []
        scenarios = Scenarios.objects.filter(
            id__in=input.scenario_ids,
            deleted=False,
        ).select_related("dataset", "simulator_agent")

        async for scenario in scenarios:
            scenario_dict = {
                "id": str(scenario.id),
                "name": scenario.name,
                "dataset_id": str(scenario.dataset_id) if scenario.dataset_id else None,
                "simulator_agent_id": (
                    str(scenario.simulator_agent_id)
                    if scenario.simulator_agent_id
                    else None
                ),
            }

            # Get row IDs from dataset if available
            if scenario.dataset_id:
                from model_hub.models.develop_dataset import Row

                row_ids = []
                async for row in Row.objects.filter(
                    dataset_id=scenario.dataset_id,
                    deleted=False,
                ).values_list("id", flat=True):
                    row_ids.append(str(row))
                scenario_dict["row_ids"] = row_ids
            else:
                scenario_dict["row_ids"] = []

            scenarios_data.append(scenario_dict)

        # Load simulator agent
        simulator_agent_data = None
        if test_execution.simulator_agent:
            simulator_agent_data = {
                "id": str(test_execution.simulator_agent.id),
                "name": test_execution.simulator_agent.name,
                "prompt": test_execution.simulator_agent.prompt,
            }

        # Load agent version with configuration snapshot
        # If agent_version not set on test_execution, get latest from agent_definition
        agent_version_data = None
        agent_version = test_execution.agent_version

        if not agent_version and test_execution.agent_definition_id:
            from simulate.models.agent_version import AgentVersion

            # Prefer active version, fall back to latest
            agent_version = (
                await AgentVersion.objects.filter(
                    agent_definition_id=test_execution.agent_definition_id,
                    status=AgentVersion.StatusChoices.ACTIVE,
                )
                .order_by("-version_number")
                .afirst()
            )
            if not agent_version:
                agent_version = (
                    await AgentVersion.objects.filter(
                        agent_definition_id=test_execution.agent_definition_id,
                    )
                    .order_by("-version_number")
                    .afirst()
                )

            # Save the resolved agent_version back to TestExecution so
            # create_call_execution_records can read it from DB
            if agent_version:
                # Warn: non-reproducible test — version resolved at runtime, not pinned.
                # Callers should pin agent_version_id on RunTest before execution.
                activity.logger.warning(
                    "agent_version_resolved_at_runtime",
                    test_execution_id=str(input.test_execution_id),
                    agent_definition_id=str(test_execution.agent_definition_id),
                    resolved_version_id=str(agent_version.id),
                    resolved_version_number=agent_version.version_number,
                )
                test_execution.agent_version = agent_version
                await test_execution.asave(update_fields=["agent_version_id"])

        if agent_version:
            agent_version_data = {
                "id": str(agent_version.id),
                "version_number": agent_version.version_number,
                "version_name": agent_version.version_name,
                "configuration_snapshot": agent_version.configuration_snapshot,
            }

        # Get workspace_id from run_test
        workspace_id = (
            str(test_execution.run_test.workspace_id)
            if test_execution.run_test.workspace_id
            else None
        )

        activity.logger.info(
            f"Setup complete: {len(scenarios_data)} scenarios, "
            f"total_rows={sum(len(s.get('row_ids', [])) for s in scenarios_data)}, "
            f"workspace_id={workspace_id}"
        )

        return SetupTestOutput(
            success=True,
            scenarios=scenarios_data,
            simulator_agent=simulator_agent_data,
            agent_version=agent_version_data,
            workspace_id=workspace_id,
        )

    except TestExecution.DoesNotExist:
        error_msg = f"Test execution not found: {input.test_execution_id}"
        activity.logger.error(error_msg)
        activity.logger.exception(error_msg)
        return SetupTestOutput(success=False, error=error_msg)

    except Exception as e:
        error_msg = f"Failed to setup test execution: {str(e)}"
        activity.logger.error(error_msg)
        activity.logger.exception(error_msg)
        return SetupTestOutput(success=False, error=error_msg)


def _parse_persona(persona_value: Any) -> dict[str, Any]:
    """Parse persona from string or dict."""
    if isinstance(persona_value, dict):
        return persona_value
    if isinstance(persona_value, str):
        try:
            return ast.literal_eval(persona_value)
        except (ValueError, SyntaxError):
            return {}
    return {}


def _build_voice_settings(
    persona_data: dict[str, Any],
    simulator_agent,
) -> dict[str, Any]:
    """Build voice settings from persona data and simulator agent defaults."""
    import os

    from ee.voice.constants.voice_catalog import resolve_voice_id
    from ee.voice.constants.voice_mapper import select_voice_id
    from tracer.models.observability_provider import ProviderChoices

    # Voice_id format is determined by the system voice provider (the
    # infrastructure hosting the simulator assistant), not by the user's
    # agent provider.
    system_provider = os.getenv("SYSTEM_VOICE_PROVIDER", ProviderChoices.LIVEKIT)

    # Select voice name based on persona attributes (rule-based scoring)
    selected_voice_name = select_voice_id(persona_data, provider=system_provider)

    # Build a provider-agnostic voice descriptor for future use
    voice_descriptor = {
        "name": selected_voice_name,
        "gender": persona_data.get("gender", "male"),
        "accent": persona_data.get("accent", ""),
        "language": persona_data.get("language", "en"),
        "age_group": persona_data.get("age_group", ""),
    }

    # Resolve the voice name to a provider-specific ID.
    # VAPI uses 11Labs voice names directly (e.g. "marissa", "phillip"),
    # so no resolution is needed. LiveKit uses Cartesia UUIDs, resolved
    # via the voice catalog adapter.
    if system_provider == ProviderChoices.VAPI or system_provider == "vapi":
        provider_voice_id = selected_voice_name
    else:
        provider_voice_id = resolve_voice_id(
            selected_voice_name,
            voice_descriptor=voice_descriptor,
        )

    # Handle background sound
    bg_sound = persona_data.get("background_sound") or persona_data.get(
        "backgroundSound"
    )
    bg_enabled = (
        isinstance(bg_sound, bool)
        and bg_sound
        or (isinstance(bg_sound, str) and bg_sound.strip().lower() == "true")
    )
    bg_value = "on" if bg_enabled else "off"
    bg_reason = "background enabled" if bg_enabled else "background disabled by persona"

    # LLM config for the simulated caller agent.
    sim_llm_model = "gpt-4.1"
    sim_llm_temperature = 0.2

    return {
        "voice_id": provider_voice_id,
        "voice_descriptor": voice_descriptor,
        "llm_model": sim_llm_model,
        "llm_temperature": sim_llm_temperature,
        "speed": persona_data.get(
            "conversation_speed", simulator_agent.conversation_speed
        ),
        "interrupt_sensitivity": (
            persona_data.get("interrupt_sensitivity")
            or persona_data.get("interruptSensitivity")
            or 5  # Default to middle of 1-10 range
        ),
        "finished_speaking_sensitivity": (
            persona_data.get("finished_speaking_sensitivity")
            or persona_data.get("finishedSpeakingSensitivity")
            or 5  # Default to middle of 1-10 range
        ),
        "max_call_duration_in_minutes": simulator_agent.max_call_duration_in_minutes,
        "initial_message_delay": (
            persona_data.get("initial_message_delay")
            or persona_data.get("initialMessageDelay")
            or simulator_agent.initial_message_delay
        ),
        "language": persona_data.get("language", "en"),
        "initial_message": simulator_agent.initial_message,
        "background_sound": bg_value,
        "background_sound_reason": bg_reason,
        "conversation_speed": (
            persona_data.get("conversation_speed")
            or persona_data.get("conversationSpeed")
            or simulator_agent.conversation_speed
        ),
        "min_endpointing_delay": persona_data.get("min_endpointing_delay", 0.5),
        "silence_timeout_seconds": persona_data.get("silence_timeout_seconds", 30),
    }


@activity.defn(name="create_call_execution_records")
async def create_call_execution_records(
    input: CreateCallRecordsInput,
) -> CreateCallRecordsOutput:
    """
    Create CallExecution and CreateCallExecution records in database.

    Creates records in batches for efficiency. Each CallExecution is created with
    PENDING status, and a corresponding CreateCallExecution is created with
    REGISTERED status for the test_monitor to pick up.

    Uses heartbeat for progress tracking since this can be long-running
    when creating thousands of records.

    Timeout: 5 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Creating call records: test_execution_id={input.test_execution_id}, "
            f"scenarios={len(input.scenarios)}"
        )

        from model_hub.models.develop_dataset import Cell, Row
        from simulate.models import Scenarios
        from simulate.models.run_test import CreateCallExecution
        from simulate.models.test_execution import CallExecution, TestExecution
        from ee.voice.utils.prompt_builder import generate_dynamic_prompt
        from tfc.settings.settings import VAPI_INDIAN_PHONE_NUMBER_ID
        from tfc.temporal.common.heartbeat import Heartbeater

        test_execution = await TestExecution.objects.select_related(
            "run_test__workspace",
            "run_test__organization",
            "agent_version",
        ).aget(id=input.test_execution_id)

        # Use agent_version from test_execution directly
        agent_version = test_execution.agent_version
        snapshot = agent_version.configuration_snapshot if agent_version else {}

        call_ids = []
        batch_size = 50  # Smaller batches due to extra processing per record
        total_to_create = sum(len(s.get("row_ids", [])) or 1 for s in input.scenarios)

        # Use heartbeat for long-running record creation
        async with Heartbeater() as heartbeater:
            heartbeater.details = ("creating_records", 0, total_to_create)

            for scenario_data in input.scenarios:
                scenario = await Scenarios.objects.select_related(
                    "simulator_agent",
                    "dataset",
                ).aget(id=scenario_data["id"])

                simulator_agent = scenario.simulator_agent
                base_prompt = simulator_agent.prompt if simulator_agent else ""

                row_ids = scenario_data.get("row_ids", [])

                # If no row IDs, create one call for the scenario
                if not row_ids:
                    row_ids = [None]

                # Create CallExecution and CreateCallExecution records
                for row_id in row_ids:
                    try:
                        # Fetch row data if row_id is provided
                        row_data = {}
                        if row_id:
                            try:
                                row = await Row.objects.aget(id=row_id)
                                # Fetch cells for this row to build row_data dict
                                cells = Cell.objects.filter(
                                    row_id=row_id
                                ).select_related("column")
                                async for cell in cells:
                                    if cell.column and cell.column.name:
                                        value = cell.value
                                        if (
                                            cell.column.name == "persona"
                                            and isinstance(value, str)
                                        ):
                                            try:
                                                value = json.loads(value)
                                            except (ValueError, TypeError):
                                                try:
                                                    value = ast.literal_eval(value)
                                                except (ValueError, SyntaxError):
                                                    # Not valid JSON or Python dict — store
                                                    # as-is. Downstream consumers already
                                                    # handle both str and dict types.
                                                    activity.logger.debug(
                                                        "persona_parse_fallback_to_str",
                                                        row_id=str(row_id),
                                                        value_preview=str(value)[:100],
                                                    )
                                        row_data[cell.column.name] = value
                            except Row.DoesNotExist:
                                activity.logger.warning(f"Row not found: {row_id}")

                        # Parse persona from row data
                        persona_data = _parse_persona(row_data.get("persona", {}))

                        # Build voice settings
                        voice_settings = (
                            _build_voice_settings(persona_data, simulator_agent)
                            if simulator_agent
                            else {}
                        )

                        # Determine phone number and call direction from config
                        call_execution_phone_number = snapshot.get("contact_number", "")
                        is_inbound = snapshot.get("inbound", True)
                        call_direction = "inbound" if is_inbound else "outbound"

                        # Build metadata
                        metadata = {
                            "run_test_id": str(test_execution.run_test_id),
                            "scenario_id": str(scenario.id),
                            "scenario_name": scenario.name,
                            "agent_definition_id": (
                                str(test_execution.agent_definition_id)
                                if test_execution.agent_definition_id
                                else None
                            ),
                            "organization_id": str(
                                test_execution.run_test.organization_id
                            ),
                            "row_id": row_id,
                            "row_data": row_data,
                            "dataset_id": (
                                str(scenario.dataset_id)
                                if scenario.dataset_id
                                else None
                            ),
                            "call_direction": call_direction,
                            "user_assistant_id": snapshot.get("assistant_id"),
                            "user_phone_number": call_execution_phone_number,
                            "user_api_key": snapshot.get("api_key"),
                        }

                        # Build call_metadata for CallExecution
                        call_metadata = {
                            "call_direction": call_direction,
                            "scenario_id": str(scenario.id),
                            "scenario_name": scenario.name,
                            "voice_settings": voice_settings,
                        }
                        if row_id:
                            call_metadata["row_id"] = row_id
                            call_metadata["row_data"] = row_data

                        # Create CallExecution record
                        call_execution = await CallExecution.objects.acreate(
                            test_execution=test_execution,
                            scenario=scenario,
                            phone_number=call_execution_phone_number,
                            status=CallExecution.CallStatus.PENDING,
                            row_id=row_id,
                            call_metadata=call_metadata,
                            agent_version=agent_version,
                        )
                        call_ids.append(str(call_execution.id))

                        # Generate system prompt using dynamic prompt builder
                        system_prompt = base_prompt
                        if row_data and simulator_agent and agent_version:
                            try:
                                system_prompt = generate_dynamic_prompt(
                                    prompt_template=base_prompt,
                                    row_data=row_data,
                                    agent_version=agent_version,
                                    call_type=call_direction,
                                )
                            except Exception as e:
                                activity.logger.warning(
                                    f"Failed to generate dynamic prompt: {e}"
                                )
                                system_prompt = base_prompt

                        # Validate that all {{tokens}} were resolved.
                        # Unresolved tokens mean the dataset row is missing columns
                        # referenced in the prompt — the call would receive garbled input.
                        unresolved = _UNRESOLVED_TOKEN_RE.findall(system_prompt)
                        if unresolved:
                            missing_cols = sorted(set(
                                t.strip("{}").strip() for t in unresolved
                            ))
                            ended_reason = (
                                f"Unresolved template variables: {missing_cols}. "
                                f"Ensure the dataset has columns matching these names."
                            )
                            activity.logger.error(
                                "call_record_unresolved_tokens",
                                row_id=str(row_id),
                                unresolved=missing_cols,
                                ended_reason=ended_reason,
                            )
                            call_execution.status = CallExecution.CallStatus.FAILED
                            call_execution.ended_reason = ended_reason
                            await call_execution.asave(update_fields=["status", "ended_reason"])
                            # Create a FAILED CreateCallExecution so the UI surfaces the error
                            await CreateCallExecution.objects.acreate(
                                call_execution=call_execution,
                                phone_number_id="",
                                to_number=call_execution_phone_number,
                                system_prompt=base_prompt,
                                metadata=metadata,
                                voice_settings=voice_settings,
                                status=CreateCallExecution.CallStatus.FAILED,
                            )
                            call_ids.append(str(call_execution.id))
                            continue

                        # Determine phone_number_id for CreateCallExecution
                        # For outbound calls, this will be empty (acquired later)
                        # For inbound calls, use VAPI phone number ID
                        phone_number_id = ""
                        if (
                            call_execution_phone_number
                            and call_execution_phone_number.startswith("+91")
                        ):
                            phone_number_id = VAPI_INDIAN_PHONE_NUMBER_ID or os.getenv(
                                "VAPI_PHONE_NUMBER_ID", ""
                            )

                        # Create CreateCallExecution record for UI display (call logs).
                        #
                        # Status lifecycle for Temporal-managed calls:
                        # 1. Created here with ONGOING - prevents test_monitor from picking up
                        #    (test_monitor only processes REGISTERED status)
                        # 2. Stays ONGOING during entire call execution
                        # 3. Updated to COMPLETED/FAILED/CANCELLED by persist_call_result activity
                        #
                        # This differs from Celery flow where:
                        # - Created with REGISTERED status
                        # - test_monitor picks up and processes
                        # - Updates to COMPLETED after call creation
                        await CreateCallExecution.objects.acreate(
                            call_execution=call_execution,
                            phone_number_id=phone_number_id,
                            to_number=call_execution_phone_number,
                            system_prompt=system_prompt,
                            metadata=metadata,
                            voice_settings=voice_settings,
                            status=CreateCallExecution.CallStatus.ONGOING,
                        )

                        heartbeater.details = (
                            "creating_records",
                            len(call_ids),
                            total_to_create,
                        )

                    except Exception as e:
                        activity.logger.error(
                            f"Failed to create call record for row {row_id}: {str(e)}"
                        )
                        # Continue with other records

        # Update test execution with total calls
        test_execution.total_calls = len(call_ids)
        await test_execution.asave(update_fields=["total_calls"])

        activity.logger.info(
            f"Created {len(call_ids)} call records (with CreateCallExecution) for "
            f"test_execution={input.test_execution_id}"
        )

        return CreateCallRecordsOutput(
            call_ids=call_ids,
            total_created=len(call_ids),
        )

    except Exception as e:
        error_msg = f"Failed to create call records: {str(e)}"
        activity.logger.error(error_msg)
        activity.logger.exception(error_msg)
        return CreateCallRecordsOutput(error=error_msg)


@activity.defn(name="get_unlaunched_call_ids")
async def get_unlaunched_call_ids(
    input: GetUnlaunchedCallsInput,
) -> GetUnlaunchedCallsOutput:
    """
    Get call IDs that haven't been launched yet.

    Used after continue-as-new to resume launching calls.
    Queries for calls with PENDING status.

    Timeout: 1 minute
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Getting unlaunched calls: test_execution_id={input.test_execution_id}"
        )

        from simulate.models.test_execution import CallExecution

        # Get calls that are still pending (not yet launched)
        call_ids = []
        async for call_id in CallExecution.objects.filter(
            test_execution_id=input.test_execution_id,
            status=CallExecution.CallStatus.PENDING,
        ).values_list("id", flat=True):
            call_ids.append(str(call_id))

        activity.logger.info(f"Found {len(call_ids)} unlaunched calls")

        return GetUnlaunchedCallsOutput(call_ids=call_ids)

    except Exception as e:
        activity.logger.error(f"Failed to get unlaunched calls: {str(e)}")
        activity.logger.exception(f"Failed to get unlaunched calls: {str(e)}")
        return GetUnlaunchedCallsOutput(call_ids=[])


@activity.defn(name="finalize_test_execution")
async def finalize_test_execution(input: FinalizeInput) -> None:
    """
    Finalize test execution with final status and counts.

    Updates TestExecution record with completion status, call counts,
    and triggers post-execution tasks (eval summary).

    Timeout: 2 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Finalizing test execution: test_execution_id={input.test_execution_id}, "
            f"status={input.status}, completed={input.completed_calls}, failed={input.failed_calls}"
        )

        from django.utils import timezone

        from simulate.models.test_execution import (
            EvalExplanationSummaryStatus,
            TestExecution,
        )

        test_execution = await TestExecution.objects.aget(id=input.test_execution_id)

        # Update final status and counts
        test_execution.status = input.status
        test_execution.completed_calls = input.completed_calls
        test_execution.failed_calls = input.failed_calls
        test_execution.completed_at = timezone.now()
        test_execution.picked_up_by_executor = False

        # Trigger eval summary if completed
        # Handle both string and enum values for status comparison
        status_value = (
            input.status.value if hasattr(input.status, "value") else input.status
        )
        if status_value in [
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.COMPLETED.value,
            "COMPLETED",
            "completed",
        ]:
            test_execution.eval_explanation_summary_status = (
                EvalExplanationSummaryStatus.PENDING
            )
            await test_execution.asave(
                update_fields=[
                    "status",
                    "completed_calls",
                    "failed_calls",
                    "completed_at",
                    "picked_up_by_executor",
                    "eval_explanation_summary_status",
                ]
            )

            try:
                from simulate.tasks.eval_summary_tasks import run_eval_summary_task

                run_eval_summary_task.apply_async(args=(str(test_execution.id),))
                activity.logger.info(
                    f"Triggered eval summary for test_execution={input.test_execution_id}"
                )
            except Exception as e:
                activity.logger.warning(f"Failed to trigger eval summary: {str(e)}")
        else:
            await test_execution.asave(
                update_fields=[
                    "status",
                    "completed_calls",
                    "failed_calls",
                    "completed_at",
                    "picked_up_by_executor",
                ]
            )

        activity.logger.info(f"Finalized test execution: {input.test_execution_id}")

    except TestExecution.DoesNotExist:
        activity.logger.error(f"Test execution not found: {input.test_execution_id}")
        activity.logger.exception(
            f"Test execution not found: {input.test_execution_id}"
        )
        raise

    except Exception as e:
        activity.logger.error(f"Failed to finalize test execution: {str(e)}")
        activity.logger.exception(f"Failed to finalize test execution: {str(e)}")
        raise


@activity.defn(name="update_test_execution_counts")
async def update_test_execution_counts(
    test_execution_id: str,
    completed_calls: int,
    failed_calls: int,
) -> None:
    """
    Update test execution progress counts.

    Called periodically to sync workflow state with database.

    Timeout: 30 seconds
    Queue: tasks_s
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        from simulate.models.test_execution import TestExecution

        await TestExecution.objects.filter(id=test_execution_id).aupdate(
            completed_calls=completed_calls,
            failed_calls=failed_calls,
        )

    except Exception as e:
        activity.logger.error(f"Failed to update test execution counts: {str(e)}")
        activity.logger.exception(f"Failed to update test execution counts: {str(e)}")
        # Don't fail - this is just a progress update


@activity.defn(name="cancel_pending_calls")
async def cancel_pending_calls(
    input: "CancelPendingCallsInput",
) -> "CancelPendingCallsOutput":
    """
    Cancel all pending/ongoing CallExecution records for a test execution.

    Called when a test execution is cancelled by the user. Updates all
    CallExecution records that are not in a final state (COMPLETED, FAILED,
    CANCELLED) to CANCELLED status with the provided reason.

    Also updates corresponding CreateCallExecution records to CANCELLED.

    Timeout: 2 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Cancelling pending calls for test_execution={input.test_execution_id}, "
            f"reason={input.reason}"
        )

        from simulate.models.run_test import CreateCallExecution
        from simulate.models.test_execution import CallExecution

        # Find all calls that are not in a final state
        # Include ANALYZING: calls that finished voice but are still running evals
        non_final_statuses = [
            CallExecution.CallStatus.PENDING,
            CallExecution.CallStatus.REGISTERED,
            CallExecution.CallStatus.ONGOING,
            CallExecution.CallStatus.ANALYZING,
        ]

        # Get call IDs that need to be cancelled
        call_ids_to_cancel = []
        async for call_id in CallExecution.objects.filter(
            test_execution_id=input.test_execution_id,
            status__in=non_final_statuses,
        ).values_list("id", flat=True):
            call_ids_to_cancel.append(call_id)

        if not call_ids_to_cancel:
            activity.logger.info(
                f"No pending calls to cancel for test_execution={input.test_execution_id}"
            )
            # Still update TestExecution status to CANCELLED
            from simulate.models.test_execution import TestExecution

            await TestExecution.objects.filter(
                id=input.test_execution_id,
            ).aupdate(status=TestExecution.ExecutionStatus.CANCELLED)
            return CancelPendingCallsOutput(cancelled_count=0)

        # Bulk update CallExecution records
        cancelled_count = await CallExecution.objects.filter(
            id__in=call_ids_to_cancel,
        ).aupdate(
            status=CallExecution.CallStatus.CANCELLED,
            ended_reason=input.reason,
        )

        # Bulk update corresponding CreateCallExecution records
        await CreateCallExecution.objects.filter(
            call_execution_id__in=call_ids_to_cancel,
        ).aupdate(
            status=CreateCallExecution.CallStatus.CANCELLED,
        )

        # Cancel child CallExecutionWorkflows
        # Child's _handle_cancellation will release slot (handles both active and pending queue)
        try:
            from simulate.temporal.constants import CALL_EXECUTION_WORKFLOW_ID_PREFIX
            from tfc.temporal.common.client import get_client

            client = await get_client()
            cancelled_workflows = 0

            for call_id in call_ids_to_cancel:
                call_id_str = str(call_id)
                workflow_id = f"{CALL_EXECUTION_WORKFLOW_ID_PREFIX}-{call_id_str}"

                try:
                    handle = client.get_workflow_handle(workflow_id)
                    # TODO: Re-evaluate cooperative signal for outbound call flow.
                    # Previously sent handle.signal(SIGNAL_CANCEL_CALL) here to
                    # set _cancelled=True and unblock wait_condition, but sending
                    # both signal + handle.cancel() caused a double-CancelledError
                    # that aborted _handle_cancellation cleanup (room not deleted,
                    # DB status not updated).  Using handle.cancel() alone for now.
                    await handle.cancel()
                    cancelled_workflows += 1
                    activity.logger.info(f"Cancelled child workflow: {workflow_id}")
                except Exception as e:
                    # Workflow already completed/failed - slot already released
                    activity.logger.info(
                        f"Could not cancel workflow {workflow_id}: {e}"
                    )

            activity.logger.info(
                f"Cancellation complete: cancelled {cancelled_workflows}/{len(call_ids_to_cancel)} workflows"
            )
        except Exception as e:
            activity.logger.error(f"Failed to cancel child workflows: {e}")
            activity.logger.exception(f"Failed to cancel child workflows: {e}")

        # Update TestExecution status to CANCELLED
        from simulate.models.test_execution import TestExecution

        await TestExecution.objects.filter(
            id=input.test_execution_id,
        ).aupdate(status=TestExecution.ExecutionStatus.CANCELLED)

        activity.logger.info(
            f"Cancelled {cancelled_count} calls for test_execution={input.test_execution_id}"
        )

        return CancelPendingCallsOutput(cancelled_count=cancelled_count)

    except Exception as e:
        activity.logger.error(f"Failed to cancel pending calls: {str(e)}")
        activity.logger.exception(f"Failed to cancel pending calls: {str(e)}")
        raise
