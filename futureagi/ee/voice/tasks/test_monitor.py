# import traceback
# from datetime import timedelta

# import structlog
# from django.db import close_old_connections
# from django.utils import timezone

# from ee.voice.services.voice_service_manager import VoiceServiceManager

# logger = structlog.get_logger(__name__)
# from simulate.models import AgentDefinition, TestExecution, agent_definition
# from simulate.models.run_test import CreateCallExecution
# from simulate.models.test_execution import CallExecution
# from simulate.services import vapi_service
# from ee.voice.services.background_sound_selector import select_background_sound
# from ee.voice.services.call_limit_service import CallLimitService
# from ee.voice.services.phone_number_service import PhoneNumberService
# from tfc.temporal.drop_in import temporal_activity
# from tracer.models.observability_provider import ProviderChoices


# def handle_stuck_call_executions():
#     """
#     Handle call executions that have been stuck in ONGOING status for more than 15 minutes.
#     Sets picked_up_by_executor=False for their related TestExecutions so they can be picked up again.
#     """
#     try:
#         # Close any existing connections to ensure fresh connection
#         close_old_connections()

#         # Calculate the cutoff time (15 minutes ago)
#         cutoff_time = timezone.now() - timedelta(minutes=15)

#         # Find CallExecutions stuck in ONGOING status for more than 15 minutes
#         # We'll use updated_at as the reference since that's when the status was last changed
#         stuck_calls = CallExecution.objects.filter(
#             status=CallExecution.CallStatus.ONGOING,
#             updated_at__lt=cutoff_time,
#             simulation_call_type=CallExecution.SimulationCallType.VOICE,
#         ).select_related("test_execution")

#         stuck_count = stuck_calls.count()

#         if stuck_count == 0:
#             logger.debug("No stuck call executions found")
#             return

#         logger.warning(
#             f"Found {stuck_count} call executions stuck in ONGOING status for >15 minutes"
#         )

#         # Get unique test execution IDs
#         test_execution_ids = list(
#             stuck_calls.values_list("test_execution_id", flat=True).distinct()
#         )

#         # Reset picked_up_by_executor for these test executions
#         updated_count = TestExecution.objects.filter(
#             id__in=test_execution_ids, picked_up_by_executor=True
#         ).update(
#             picked_up_by_executor=False, status=TestExecution.ExecutionStatus.RUNNING
#         )

#         logger.warning(
#             f"Reset picked_up_by_executor=False for {updated_count} test executions due to stuck calls"
#         )

#         # Log details for each stuck call
#         for call in stuck_calls:
#             time_stuck = timezone.now() - call.updated_at
#             logger.warning(
#                 f"Stuck call: ID={call.id}, TestExecution={call.test_execution_id}, "
#                 f"ProviderCallID={call.service_provider_call_id}, StuckFor={time_stuck.total_seconds()/60:.1f}min"
#             )

#     except Exception as e:
#         logger.error(f"Error in handle_stuck_call_executions: {str(e)}")
#     finally:
#         # Ensure connections are closed after the operation
#         close_old_connections()


# def monitor_single_test_execution_task(test_execution_id):
#     """
#     Temporal activity to monitor a single test execution.
#     """
#     try:
#         close_old_connections()
#         # Lazy import to avoid circular dependency
#         from simulate.services.test_executor import TestExecutor

#         # Prefetch related data needed for monitoring to avoid N+1 queries
#         task = (
#             TestExecution.objects.select_related(
#                 "run_test__agent_definition", "run_test__organization"
#             )
#             .only(
#                 "id",
#                 "status",
#                 "picked_up_by_executor",
#                 "eval_explanation_summary_status",
#                 "run_test__id",
#                 "run_test__agent_definition__id",
#                 "run_test__agent_definition__api_key",
#                 # "run_test__agent_definition__latest_version_id",
#                 "run_test__organization__id",
#                 "run_test__organization__name",
#             )
#             .get(id=test_execution_id)
#         )
#         test_executor = TestExecutor()
#         test_executor.monitor_test_execution(task)
#         return True
#     except Exception as e:
#         logger.error(f"Error monitoring test execution {test_execution_id}: {str(e)}")
#         traceback.print_exc()
#         return False
#     finally:
#         close_old_connections()


# @temporal_activity(
#     time_limit=3600,
#     max_retries=0,
#     retry_delay=300,
#     queue="tasks_s",
# )
# def create_call_executions():
#     """
#     Temporal activity to create call executions for all active test executions.
#     This task runs every minute to create call executions for all active test executions.
#     Now includes call limiting logic for both application and organization levels.
#     Also handles stuck call executions that have been in ONGOING status for >30 minutes.
#     """
#     try:
#         close_old_connections()

#         # Handle stuck call executions first
#         handle_stuck_call_executions()

#         # Cleanup Phone Numbers
#         PhoneNumberService.cleanup_phone_numbers()

#         # # # Log current call limits status
#         # CallLimitService.log_call_limits_status()

#         # Get processable call executions based on limits
#         processable_calls = CallLimitService.get_prioritized_call_executions()

#         if not processable_calls:
#             logger.info(
#                 "No call executions can be processed due to call limits or no registered calls"
#             )
#             return

#         logger.info(
#             f"Processing {len(processable_calls)} call executions based on call limits"
#         )

#         vapi_service_instance = vapi_service.VapiService()
#         vsm_instance = VoiceServiceManager()
#         successful_calls = 0
#         failed_calls = 0

#         for call_exec in processable_calls:
#             try:
#                 # Get organization info for logging
#                 org_id = (
#                     call_exec.call_execution.test_execution.run_test.organization_id
#                 )
#                 org_name = (
#                     call_exec.call_execution.test_execution.run_test.organization.name
#                 )

#                 # Double-check limits before creating call
#                 can_create = CallLimitService.can_create_call(org_id)
#                 if not can_create["can_create"]:
#                     logger.info(
#                         f"Skipping call execution {call_exec.id} for org {org_name}: {can_create['reason']}"
#                     )
#                     continue

#                 # Update call execution status to ONGOING
#                 call_exec.status = CreateCallExecution.CallStatus.COMPLETED
#                 call_exec.save()

#                 new_call_execution = CallExecution.objects.filter(
#                     id=call_exec.call_execution_id
#                 ).get()

#                 if new_call_execution.status == CallExecution.CallStatus.CANCELLED:
#                     continue

#                 def _compute_background_settings() -> dict:
#                     """Recompute background sound now that the call will proceed."""
#                     # print(f"[BG] Recomputing background sound for call_exec {call_exec.id}")
#                     # print(f"[BG] call_exec before BG update: {call_exec.id} | voice_settings={call_exec.voice_settings} | metadata={call_exec.metadata}")
#                     voice_settings_local = call_exec.voice_settings or {}
#                     row_data_local = call_exec.metadata.get("row_data", {}) or {}

#                     # Respect the flag from executor: must be exactly "on" or "off"
#                     raw_flag = voice_settings_local.get("background_sound", "off")
#                     bg_flag = str(raw_flag).strip().lower()
#                     if bg_flag not in {"on", "off"}:
#                         # print(f"[BG] Invalid background_sound flag ({raw_flag}); defaulting to off")
#                         bg_flag = "off"
#                     if bg_flag == "off":
#                         # Caller has disabled background noise; keep off and skip LLM selection
#                         # print(f"[BG] Background sound flagged off; keeping value={bg_flag}")
#                         voice_settings_local["background_sound"] = "off"
#                         voice_settings_local["background_sound_reason"] = (
#                             voice_settings_local.get(
#                                 "background_sound_reason", "background disabled"
#                             )
#                         )
#                         return voice_settings_local

#                     situation_text = (
#                         row_data_local.get("situation") or "General phone call"
#                     )
#                     # Invoke LLM to select the background sound based on the current situation
#                     bg_selection_local = (
#                         select_background_sound(situation=situation_text) or {}
#                     )

#                     logger.info(
#                         f"[BG] Selected background_sound={bg_selection_local.get('value')} ; reason={bg_selection_local.get('reason')}"
#                     )
#                     voice_settings_local["background_sound"] = bg_selection_local.get(
#                         "value", "office"
#                     )
#                     voice_settings_local["background_sound_reason"] = (
#                         bg_selection_local.get("reason", "fallback to office")
#                     )
#                     # Persist updated voice settings on both CreateCallExecution and CallExecution metadata
#                     call_exec.voice_settings = voice_settings_local
#                     call_exec.save(update_fields=["voice_settings"])
#                     call_exec.call_execution.call_metadata["voice_settings"] = (
#                         voice_settings_local
#                     )
#                     call_exec.call_execution.save(update_fields=["call_metadata"])
#                     logger.info(
#                         f"[BG] Updated voice_settings on call_exec {call_exec.id}: "
#                         f"{voice_settings_local.get('background_sound')} | voice_settings={call_exec.voice_settings} | metadata={call_exec.metadata}"
#                     )
#                     return voice_settings_local

#                 is_outbound = call_exec.metadata.get("call_direction") == "outbound"

#                 if is_outbound:
#                     logger.info(f"Creating outbound call for {call_exec.id}")

#                     try:
#                         simulation_phone = PhoneNumberService.acquire_phone_number(
#                             call_direction="outbound",
#                             call_execution=call_exec.call_execution,
#                         )

#                         logger.info(
#                             f"Acquired phone {simulation_phone.phone_number} for outbound call"
#                         )

#                     except ValueError as e:
#                         # No phones available - keep call in REGISTERED status and retry later
#                         logger.info(
#                             f"No phones available for outbound call {call_exec.id}: {str(e)}. Will retry."
#                         )
#                         # Don't mark as failed - just skip and let it retry next minute
#                         call_exec.status = CreateCallExecution.CallStatus.REGISTERED
#                         call_exec.save()
#                         continue

#                     # Background sound selection now that we're cleared to create the call
#                     voice_settings = _compute_background_settings()
#                     # print(f"[BG] Voice settings after compute (outbound): {voice_settings}")

#                     # Step 2: Update CallExecution with phone number
#                     call_exec.call_execution.phone_number = (
#                         simulation_phone.phone_number
#                     )
#                     call_exec.call_execution.call_metadata[
#                         "simulation_phone_number"
#                     ] = simulation_phone.phone_number
#                     call_exec.call_execution.call_metadata["simulation_phone_id"] = str(
#                         simulation_phone.id
#                     )
#                     call_exec.call_execution.save()

#                     # Update CreateCallExecution with phone info
#                     call_exec.phone_number_id = simulation_phone.vapi_phone_number_id
#                     call_exec.to_number = simulation_phone.phone_number
#                     call_exec.save()

#                     # Step 3: Create simulation assistant with SYSTEM credentials
#                     system_vapi_service = vapi_service.VapiService()

#                     simulation_assistant = system_vapi_service.create_assistant(
#                         name=f"Sim Customer - {call_exec.metadata.get('scenario_name', 'Unknown')}",
#                         system_prompt=call_exec.system_prompt,
#                         voice_settings=voice_settings,
#                         background_sound=voice_settings.get("background_sound"),
#                         language=voice_settings.get("language", "en-US"),
#                     )

#                     simulation_assistant_id = simulation_assistant.get("id")
#                     if not simulation_assistant_id:
#                         PhoneNumberService.release_phone_number(simulation_phone.id)
#                         raise Exception(
#                             "Failed to get simulation assistant ID from creation response"
#                         )

#                     logger.info(
#                         f"Created simulation assistant: {simulation_assistant_id}"
#                     )

#                     # Step 4: Assign simulation assistant to phone number
#                     system_vapi_service.assign_assistant_to_phone(
#                         phone_number_id=simulation_phone.vapi_phone_number_id,
#                         assistant_id=simulation_assistant_id,
#                     )

#                     logger.info(
#                         f"Assigned simulation assistant {simulation_assistant_id} to phone {simulation_phone.phone_number}"
#                     )

#                     # Step 5: Create outbound call using USER's credentials
#                     user_api_key = (
#                         call_exec.metadata.get("user_api_key")
#                         or call_exec.call_execution.test_execution.run_test.agent_definition.api_key
#                     )
#                     user_assistant_id = (
#                         call_exec.metadata.get("user_assistant_id")
#                         or call_exec.call_execution.test_execution.run_test.agent_definition.assistant_id
#                     )
#                     user_phone_number = (
#                         call_exec.metadata.get("user_phone_number")
#                         or call_exec.call_execution.test_execution.run_test.agent_definition.contact_number
#                     )

#                     # if api key or assistant id or phone number is not found, raise an error and release the phone number
#                     if (
#                         not user_api_key
#                         or not user_assistant_id
#                         or not user_phone_number
#                     ):
#                         PhoneNumberService.release_phone_number(simulation_phone.id)
#                         raise Exception(
#                             "No user API key, assistant id, or phone number found"
#                         )

#                     user_vapi_service = vapi_service.VapiService(api_key=user_api_key)
#                     vapi_response = user_vapi_service.create_outbound_call(
#                         assistant_id=user_assistant_id,
#                         from_phone_number=user_phone_number,  # Phone number (not ID) - will be looked up
#                         to_phone_number=simulation_phone.phone_number,
#                         metadata={
#                             "test_type": "outbound_simulation",
#                             "scenario_id": call_exec.metadata.get("scenario_id"),
#                             "row_id": call_exec.metadata.get("row_id"),
#                         },
#                     )

#                     # Step 6: Store provider response (provider-neutral)
#                     call_exec.call_execution.service_provider_call_id = vapi_response[
#                         "id"
#                     ]
#                     call_exec.call_execution.provider_call_data = {
#                         ProviderChoices.VAPI.value: vapi_response
#                     }
#                     call_exec.call_execution.status = CallExecution.CallStatus.ONGOING
#                     call_exec.call_execution.call_metadata[
#                         "simulation_assistant_id"
#                     ] = simulation_assistant_id
#                     call_exec.call_execution.save()

#                     successful_calls += 1
#                     logger.info(
#                         f"Successfully created outbound call for org {org_name} (ID: {org_id})"
#                     )

#                 else:
#                     new_call_execution.status = CallExecution.CallStatus.ONGOING
#                     new_call_execution.save()

#                     # Background sound selection now that we're cleared to create the call
#                     voice_settings = _compute_background_settings()
#                     # print(f"[BG] Voice settings after compute (inbound): {voice_settings}")

#                     # Create the phone call
#                     vapi_response = vapi_service_instance.create_phone_call(
#                         phone_number_id=call_exec.phone_number_id,
#                         to_number=call_exec.to_number,
#                         system_prompt=call_exec.system_prompt,
#                         metadata=call_exec.metadata,
#                         voice_settings=voice_settings,
#                     )

#                     call_exec.call_execution.service_provider_call_id = vapi_response[
#                         "id"
#                     ]
#                     call_exec.call_execution.provider_call_data = {
#                         ProviderChoices.VAPI: vapi_response
#                     }
#                     call_exec.call_execution.status = CallExecution.CallStatus.ONGOING

#                     call_exec.call_execution.save()

#                     successful_calls += 1
#                     logger.info(
#                         f"Successfully created inbound call for org {org_name} (ID: {org_id})"
#                     )

#             except Exception as e:
#                 failed_calls += 1
#                 logger.error(
#                     f"Failed to create call for execution {call_exec.id}: {str(e)}"
#                 )
#                 traceback.print_exc()

#                 # Release phone number if it was acquired for an outbound call
#                 try:
#                     is_outbound = call_exec.metadata.get("call_direction") == "outbound"
#                     if is_outbound:
#                         # Check if phone was acquired (will be in call_metadata if acquired)
#                         simulation_phone_id = (
#                             call_exec.call_execution.call_metadata.get(
#                                 "simulation_phone_id"
#                             )
#                         )
#                         if simulation_phone_id:
#                             PhoneNumberService.release_phone_number(simulation_phone_id)
#                             logger.info(
#                                 f"Released phone after failure for call {call_exec.id}"
#                             )
#                 except Exception as release_error:
#                     logger.error(
#                         f"Error releasing phone after failure: {str(release_error)}"
#                     )

#                 # Update status to FAILED
#                 call_exec.status = CreateCallExecution.CallStatus.FAILED
#                 call_exec.save()

#                 call_exec.call_execution.status = CallExecution.CallStatus.FAILED
#                 call_exec.call_execution.ended_reason = (
#                     "Failed to create call. Please rerun the call."
#                 )
#                 call_exec.call_execution.ended_at = timezone.now()
#                 call_exec.call_execution.save()

#         logger.info(
#             f"Call execution creation completed. Successful: {successful_calls}, Failed: {failed_calls}"
#         )

#     except Exception as e:
#         logger.error(f"Error in create_call_executions task: {e}")
#         traceback.print_exc()
#     finally:
#         close_old_connections()


# @temporal_activity(
#     time_limit=3600,
#     max_retries=0,
#     retry_delay=300,
#     queue="tasks_s",
# )
# def monitor_test_executions():
#     """
#     Temporal activity to monitor all active test executions and update their status.
#     This task runs every minute to check the status of all running test executions.
#     """
#     try:
#         close_old_connections()

#         # Get all active test executions
#         active_test_executions = TestExecution.objects.filter(
#             status__in=[
#                 TestExecution.ExecutionStatus.PENDING,
#                 TestExecution.ExecutionStatus.RUNNING,
#                 TestExecution.ExecutionStatus.EVALUATING,
#             ],
#             picked_up_by_executor=False,
#             agent_definition__agent_type=AgentDefinition.AgentTypeChoices.VOICE,
#         )

#         active_test_executions = list(
#             active_test_executions.values_list("id", flat=True)
#         )

#         # Check if there are any pending executions before updating
#         if not active_test_executions:
#             logger.info("No active test executions to monitor")
#             return

#         # Update only PENDING and RUNNING statuses to RUNNING, leave EVALUATING unchanged
#         TestExecution.objects.filter(
#             id__in=active_test_executions,
#             status__in=[
#                 TestExecution.ExecutionStatus.PENDING,
#                 TestExecution.ExecutionStatus.RUNNING,
#             ],
#             agent_definition__agent_type=AgentDefinition.AgentTypeChoices.VOICE,
#         ).update(
#             status=TestExecution.ExecutionStatus.RUNNING, picked_up_by_executor=True
#         )

#         # Process test executions using Temporal activities
#         logger.info(
#             f"Submitting {len(active_test_executions)} test executions for monitoring"
#         )

#         for test_execution_id in active_test_executions:
#             monitor_single_test_execution_task(test_execution_id=test_execution_id)

#         logger.info(
#             f"Submitted {len(active_test_executions)} test executions for monitoring"
#         )

#     except Exception as e:
#         logger.error(f"Error in monitor_test_executions task: {e}")
#         traceback.print_exc()
#     finally:
#         close_old_connections()
