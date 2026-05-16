"""
XL queue activities (tasks_xl).

Resource-intensive operations for evaluations, LLM analysis, and computationally
expensive tasks. These activities run on the tasks_xl queue with long timeouts
(up to 30 minutes) and use activity heartbeats for reliability.

All activities use async functions with Django's async ORM for non-blocking operations.

NOTE: This module reuses existing evaluation logic from:
- simulate.services.test_executor._run_simulate_evaluations_task (Celery task)
- simulate.tasks.eval_summary_tasks.run_eval_summary_task (Celery task)

These are already decorated with @temporal_activity and can be called directly
from Temporal workflows.

IMPORTANT: Each activity calls _close_old_connections() at the start to prevent
connection pool exhaustion when using PgBouncer. Without this, connections
accumulate and hit PgBouncer's pool limit (~20 by default).
"""

import asyncio
import traceback
from itertools import chain
from uuid import uuid4

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from temporalio import activity

from simulate.models.test_execution import CallExecution
from simulate.temporal.types.activities import (
    RunSimulateEvaluationsInput,
    RunSimulateEvaluationsOutput,
    RunToolCallEvaluationInput,
    RunToolCallEvaluationOutput,
)
from simulate.utils.eval_summary import derive_kpi_output_type

logger = structlog.get_logger(__name__)

# ============================================================================
# EVALUATION ACTIVITIES
# ============================================================================
# NOTE: Evaluation activities are already implemented as Celery tasks with
# @temporal_activity decorator in:
# - simulate.services.test_executor._run_simulate_evaluations_task
# - simulate.tasks.eval_summary_tasks.run_eval_summary_task
#
# These can be called directly from workflows. No additional wrapper needed.
#
# Example usage in workflow:
#   from simulate.services.test_executor import _run_simulate_evaluations_task
#   result = await workflow.execute_activity(
#       _run_simulate_evaluations_task,
#       args=[call_id],
#       start_to_close_timeout=timedelta(minutes=30),
#       task_queue="tasks_xl"
#   )
# ============================================================================


@activity.defn(name="run_simulate_evaluations")
async def run_simulate_evaluations(
    input: RunSimulateEvaluationsInput,
) -> RunSimulateEvaluationsOutput:
    """
    Standalone evaluation activity — runs all configured SimulateEvalConfig
    evaluations for a call execution without using TestExecutor.

    Replaces the previous TestExecutor wrapper. Tool evaluation is now handled
    by the separate run_tool_call_evaluation activity.

    Timeout: up to 3 hours (with heartbeats every ~30 seconds)
    Queue: tasks_xl
    """
    close_old_connections()

    from tfc.temporal.common.heartbeat import Heartbeater

    async with Heartbeater(factor=4) as heartbeater:
        heartbeater.details = (f"running evaluations for {input.call_execution_id}",)

        try:
            activity.logger.info(
                f"Running simulate evaluations for call execution {input.call_execution_id}"
            )

            # Check for cancellation before starting
            if activity.is_cancelled():
                activity.logger.info(
                    f"Activity cancelled before starting evaluations for {input.call_execution_id}"
                )
                raise asyncio.CancelledError("Activity cancelled before starting")

            # Fetch call execution asynchronously.
            # The related objects below are consumed inside
            # _run_single_evaluation to build the eval `context_map` — any
            # field accessed there needs to be prefetched here to avoid
            # N+1 queries and sync I/O inside the loop.
            heartbeater.details = ("fetching_call_execution", input.call_execution_id)
            call_execution = await CallExecution.objects.select_related(
                "test_execution",
                "test_execution__agent_definition",
                "test_execution__agent_version",
                "test_execution__run_test",
                "test_execution__run_test__organization",
                "test_execution__run_test__workspace",
                "test_execution__run_test__agent_definition",
                "test_execution__run_test__simulator_agent",
                "test_execution__run_test__prompt_template",
                "agent_version",
                "scenario",
            ).aget(id=input.call_execution_id)

            # Update status to ANALYZING to indicate evaluation is in progress
            call_execution.status = CallExecution.CallStatus.ANALYZING
            await call_execution.asave(update_fields=["status"])
            activity.logger.info(
                f"Set call execution {input.call_execution_id} status to ANALYZING"
            )

            # Check for cancellation before running evaluations
            if activity.is_cancelled():
                activity.logger.info(
                    f"Activity cancelled before running evaluations for {input.call_execution_id}"
                )
                call_execution.status = CallExecution.CallStatus.CANCELLED
                await call_execution.asave(update_fields=["status"])
                raise asyncio.CancelledError("Activity cancelled before evaluations")

            # Run evaluations standalone (no TestExecutor)
            heartbeater.details = ("running_evaluations", input.call_execution_id)

            def run_evaluations_sync():
                _run_evaluations_standalone(
                    call_execution,
                    eval_config_ids=input.eval_config_ids,
                    skip_existing=input.skip_existing,
                )

            await sync_to_async(run_evaluations_sync, thread_sensitive=False)()

            # NOTE: Do NOT update status to COMPLETED here!
            # This activity runs in parallel with CSAT calculation.
            # The workflow will update status to COMPLETED after all activities finish.

            activity.logger.info(
                f"Successfully completed simulate evaluations for call execution {input.call_execution_id}"
            )
            return RunSimulateEvaluationsOutput(success=True)

        except asyncio.CancelledError:
            activity.logger.info(
                f"Evaluations cancelled for call execution {input.call_execution_id}"
            )
            raise

        except CallExecution.DoesNotExist:
            activity.logger.error(
                f"Call execution not found for evaluations: {input.call_execution_id}"
            )
            return RunSimulateEvaluationsOutput(
                success=False,
                error=f"Call execution not found: {input.call_execution_id}",
            )

        except Exception as e:
            activity.logger.error(
                f"Error running simulate evaluations for call execution {input.call_execution_id}: {str(e)}"
            )
            activity.logger.exception(
                f"Error running simulate evaluations for call execution {input.call_execution_id}: {str(e)}"
            )
            return RunSimulateEvaluationsOutput(
                success=False,
                error=str(e),
            )

        finally:
            close_old_connections()


# ============================================================================
# STANDALONE EVALUATION HELPERS
# ============================================================================
# These functions replicate the evaluation logic from TestExecutor without
# depending on the TestExecutor class. They are called by the activities above.
# ============================================================================


def _build_transcript_data(call_execution):
    """
    Build transcript data dict from DB records.

    Assembles transcript text from CallTranscript (VOICE) or ChatMessageModel (TEXT)
    records, and reads recording URLs from call_execution fields.
    """
    from simulate.models import CallTranscript, ChatMessageModel

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
        # Build transcript text from DB records
        is_text = (
            call_execution.simulation_call_type == CallExecution.SimulationCallType.TEXT
        )

        if is_text:
            transcripts = call_execution.chat_messages.all().order_by("created_at")
        else:
            transcripts = call_execution.transcripts.all().order_by("start_time_ms")

        if transcripts.exists():
            transcript_text = []
            user_chat_transcript_text = []
            assistant_chat_transcript_text = []

            # Add context information for evaluation agent
            context_info = []
            call_metadata = call_execution.call_metadata or {}
            if call_metadata.get("agent_description"):
                context_info.append(
                    f"AGENT PROMPT: {call_metadata.get('agent_description')}"
                )
            if call_metadata.get("dynamic_prompt"):
                context_info.append(
                    f"SIMULATOR AGENT PROMPT USED: {call_metadata.get('dynamic_prompt')}"
                )
            if call_metadata.get("language"):
                context_info.append(
                    f"LANGUAGE REQUESTED: {call_metadata.get('language')}"
                )
            if call_metadata.get("initial_message"):
                context_info.append(
                    f"INITIAL MESSAGE REQUESTED: {call_metadata.get('initial_message')}"
                )

            if context_info:
                transcript_text.append("=== CALL CONTEXT ===")
                transcript_text.extend(context_info)
                transcript_text.append("=== TRANSCRIPT ===")

            if is_text:
                chat_messages_list = list(transcripts)
                for chat_message in chat_messages_list:
                    if isinstance(chat_message, str):
                        continue
                    if not hasattr(chat_message, "role") or not hasattr(
                        chat_message, "messages"
                    ):
                        continue
                    if chat_message.messages:
                        for message in chat_message.messages:
                            if not isinstance(message, str):
                                message = str(message)
                            transcript_text.append(f"{chat_message.role}: {message}")
                            if chat_message.role == "user":
                                user_chat_transcript_text.append(message)
                            elif chat_message.role == "assistant":
                                assistant_chat_transcript_text.append(message)
            else:
                try:
                    from ee.voice.utils.transcript_roles import SpeakerRoleResolver
                except ImportError:
                    SpeakerRoleResolver = None
                    logger.warning(
                        "speaker_role_resolver_unavailable_for_xl_transcript",
                        call_execution_id=str(call_execution.id),
                    )
                else:
                    provider = SpeakerRoleResolver.detect_provider(
                        call_execution.provider_call_data
                    )
                    call_dir = (call_execution.call_metadata or {}).get(
                        "call_direction", ""
                    )
                    is_outbound = str(call_dir).strip().lower() == "outbound"

                for transcript in transcripts:
                    if transcript.content.strip():
                        if SpeakerRoleResolver is None:
                            eval_role = transcript.speaker_role
                        else:
                            eval_role = SpeakerRoleResolver.get_eval_role_label(
                                transcript.speaker_role,
                                provider=provider,
                                is_outbound=is_outbound,
                            )
                        transcript_text.append(f"{eval_role}: {transcript.content}")

            transcript_data["transcript"] = "\n".join(transcript_text)
            transcript_data["user_chat_transcript"] = "\n".join(
                user_chat_transcript_text
            )
            transcript_data["assistant_chat_transcript"] = "\n".join(
                assistant_chat_transcript_text
            )

        # Read recording URLs from call_execution fields (already stored by Phase 4)
        if call_execution.recording_url:
            transcript_data["voice_recording"] = call_execution.recording_url
        if call_execution.stereo_recording_url:
            transcript_data["stereo_recording"] = call_execution.stereo_recording_url

        # Read assistant/customer recordings from provider_call_data
        if call_execution.provider_call_data:
            for (
                provider_key,
                provider_data,
            ) in call_execution.provider_call_data.items():
                if not isinstance(provider_data, dict):
                    continue

                # Pre-stored S3 recording URLs
                recording = provider_data.get("recording", {})
                if isinstance(recording, dict):
                    if (
                        recording.get("assistant")
                        and not transcript_data["assistant_recording"]
                    ):
                        transcript_data["assistant_recording"] = recording["assistant"]
                    if (
                        recording.get("customer")
                        and not transcript_data["customer_recording"]
                    ):
                        transcript_data["customer_recording"] = recording["customer"]
                    if (
                        recording.get("stereo")
                        and not transcript_data["stereo_recording"]
                    ):
                        transcript_data["stereo_recording"] = recording["stereo"]
                    if (
                        recording.get("combined")
                        and not transcript_data["voice_recording"]
                    ):
                        transcript_data["voice_recording"] = recording["combined"]

    except Exception as e:
        logger.error(f"Error building transcript data: {str(e)}")
        traceback.print_exc()

    return transcript_data


# Each context_map entry is emitted under BOTH the legacy underscore key
# and the dot-hierarchy key. Saved configs from before 2026-04-13 persist
# underscore strings (`agent_name`), new configs persist the dot form
# (`agent.name`); both must resolve. Keep additions in sync with the
# frontend flatteners in SimulationTestMode.jsx /
# CreateSimulationPreviewMode.jsx and the syntheticEvalVocabulary list in
# CreateRunTestPage.jsx.
CONTEXT_MAP_DOT_ALIASES = {
    "simulation_name": "simulation.name",
    "simulation_type": "simulation.type",
    "simulation_call_type": "simulation.call_type",
    "agent_name": "agent.name",
    "agent_type": "agent.type",
    "agent_provider": "agent.provider",
    "agent_contact_number": "agent.contact_number",
    "agent_model": "agent.model",
    "agent_language": "agent.language",
    "agent_description": "agent.description",
    "persona_name": "persona.name",
    "persona_prompt": "persona.prompt",
    "persona_description": "persona.description",
    "persona_voice_name": "persona.voice_name",
    "persona_model": "persona.model",
    "persona_initial_message": "persona.initial_message",
    "prompt_template": "prompt.name",
    "prompt_template_name": "prompt.name",
    "prompt_template_description": "prompt.description",
    "scenario_info_name": "scenario.name",
    "scenario_info_description": "scenario.description",
    "scenario_info_type": "scenario.type",
    "scenario_info_source": "scenario.source",
    "call_summary": "call.summary",
    "ended_reason": "call.ended_reason",
    "duration_seconds": "call.duration_seconds",
    "status": "call.status",
    "phone_number": "call.phone_number",
    "overall_score": "call.overall_score",
    "recording_url": "call.recording_url",
    "stereo_recording_url": "call.stereo_recording_url",
}


# Runtime keys built by `_build_transcript_data` (not on the CallExecution
# model). Mapping translates the dot-form a new frontend config emits into
# the transcript_data key the resolver already knows how to look up. The
# original underscore keys remain handled by the dedicated branches in
# `_run_single_evaluation` for full backcompat.
TRANSCRIPT_DOT_ALIASES = {
    "call.transcript": "transcript",
    "call.voice_recording": "voice_recording",
    "call.stereo_recording": "stereo_recording",
    "call.assistant_recording": "assistant_recording",
    "call.customer_recording": "customer_recording",
    "call.user_chat_transcript": "user_chat_transcript",
    "call.assistant_chat_transcript": "assistant_chat_transcript",
    # agent_prompt is resolved from the agent_version snapshot, not
    # transcript_data — handled inline in _run_single_evaluation.
    "call.agent_prompt": "agent_prompt",
}


def _build_simulation_context_map(call_execution, agent_version):
    """
    Build a flat {key: string} map of simulation context that eval configs
    can bind variables to. The key set is the contract with the frontend
    variable-mapping dropdown in `SimulationTestMode.jsx`; only keys present
    here are resolvable at run time. All FK access relies on the
    select_related in `run_simulate_evaluations` — do NOT add fields here
    without also prefetching them there.

    Each logical field is exposed under BOTH its legacy underscore key
    (`agent_name`) and its dot-hierarchy key (`agent.name`) so pre-migration
    saved eval configs and new dot-notation configs both resolve.
    """
    test_execution = call_execution.test_execution
    run_test = test_execution.run_test
    agent_def = test_execution.agent_definition or run_test.agent_definition
    simulator_agent = run_test.simulator_agent
    prompt_template = run_test.prompt_template
    config_snapshot = (
        agent_version.configuration_snapshot
        if agent_version and agent_version.configuration_snapshot
        else {}
    )

    def _s(v):
        return "" if v is None else str(v)

    # Call-level keys use the raw CallExecution field names so they line up
    # with what the frontend dropdown shows (SimulationTestMode.jsx feeds
    # raw `canonicalEntries(callData)` into the callDetail table). Hand-
    # flattened keys below (simulation_*, agent_*, persona_*, prompt_*)
    # match the explicit flattening block in the frontend, not the raw
    # serializer field names.
    ctx = {
        "simulation_name": _s(run_test.name),
        "simulation_type": _s(run_test.source_type),
        "call_summary": _s(call_execution.call_summary),
        "ended_reason": _s(call_execution.ended_reason),
        "duration_seconds": _s(call_execution.duration_seconds),
        "status": _s(call_execution.status),
        "simulation_call_type": _s(call_execution.simulation_call_type),
        "phone_number": _s(call_execution.phone_number),
        "overall_score": _s(call_execution.overall_score),
        "recording_url": _s(call_execution.recording_url),
        "stereo_recording_url": _s(call_execution.stereo_recording_url),
    }

    if agent_def:
        ctx["agent_name"] = _s(agent_def.agent_name)
        ctx["agent_type"] = _s(agent_def.agent_type)
        ctx["agent_provider"] = _s(agent_def.provider)
        ctx["agent_contact_number"] = _s(agent_def.contact_number)
        ctx["agent_language"] = _s(agent_def.language)
        ctx["agent_description"] = _s(getattr(agent_def, "description", ""))
        ctx["agent_model"] = _s(getattr(agent_def, "model", ""))

    # Snapshot fields override the live AgentDefinition when present, so
    # evals see the exact prompt/model the call actually ran with instead
    # of whatever the definition has drifted to since.
    if config_snapshot:
        snap_model = config_snapshot.get("model")
        snap_desc = config_snapshot.get("description")
        if snap_model:
            ctx["agent_model"] = _s(snap_model)
        if snap_desc:
            ctx["agent_description"] = _s(snap_desc)

    ctx.setdefault("agent_model", "")
    ctx.setdefault("agent_description", "")

    if simulator_agent:
        ctx["persona_name"] = _s(simulator_agent.name)
        ctx["persona_prompt"] = _s(simulator_agent.prompt)
        # Back-compat alias: the UI dropdown has historically shown
        # "persona_description" (serializer exposed `prompt` under a
        # `description` alias). Keep the mapping value resolvable.
        ctx["persona_description"] = _s(simulator_agent.prompt)
        ctx["persona_voice_name"] = _s(simulator_agent.voice_name)
        ctx["persona_model"] = _s(simulator_agent.model)
        ctx["persona_initial_message"] = _s(simulator_agent.initial_message)

    if prompt_template:
        ctx["prompt_template_name"] = _s(prompt_template.name)
        ctx["prompt_template_description"] = _s(prompt_template.description)
        # Back-compat alias: the frontend flattens this as "prompt_template".
        ctx["prompt_template"] = _s(prompt_template.name)

    # Scenario-row metadata (Scenarios FK on CallExecution). Dataset cell
    # values are still resolved via the scenario-column-UUID branch in the
    # main loop — these keys only cover the Scenarios row itself, prefixed
    # `scenario_info_` so they don't collide with user-named dataset
    # columns like `scenario_name`.
    scenario = getattr(call_execution, "scenario", None)
    if scenario:
        ctx["scenario_info_name"] = _s(scenario.name)
        ctx["scenario_info_description"] = _s(scenario.description)
        ctx["scenario_info_type"] = _s(scenario.scenario_type)
        ctx["scenario_info_source"] = _s(scenario.source)

    # Expose every entry under its dot-hierarchy alias too. This lets the
    # new frontend dropdowns persist `agent.name` style mapping values
    # while pre-migration configs with `agent_name` keep resolving.
    for underscore_key, dot_key in CONTEXT_MAP_DOT_ALIASES.items():
        if underscore_key in ctx:
            ctx[dot_key] = ctx[underscore_key]

    return ctx


def _run_single_evaluation(eval_config, call_execution, transcript_data):
    """
    Run a single SimulateEvalConfig evaluation.

    Replicates TestExecutor._run_single_simulate_evaluation() logic.
    """
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.tasks.user_evaluation import trigger_error_localization_for_simulate
    from model_hub.views.utils.evals import run_eval_func
    from simulate.models import Scenarios, SimulateEvalConfig
    from tfc.utils.error_codes import get_specific_error_message

    try:
        close_old_connections()

        eval_template = eval_config.eval_template

        # Prepare mapping with transcript and recording data
        mapping = eval_config.mapping.copy() if eval_config.mapping else {}

        # Get scenario column order
        scenario_ids = call_execution.test_execution.scenario_ids
        scenario_column_order_qs = (
            Scenarios.objects.filter(id__in=scenario_ids, deleted=False)
            .select_related("dataset")
            .values_list("dataset__column_order", flat=True)
        )
        scenario_column_order_list = list(chain.from_iterable(scenario_column_order_qs))

        # Get agent_version with fallback
        # Prefer test_execution.agent_definition, fallback to run_test.agent_definition
        agent_version = call_execution.agent_version
        if not agent_version:
            agent_def = call_execution.test_execution.agent_definition
            if not agent_def:
                agent_def = call_execution.test_execution.run_test.agent_definition
            if agent_def:
                agent_version = agent_def.latest_version

        # Pre-fetch data to avoid N+1 queries inside the loop
        known_keys = {
            "transcript",
            "voice_recording",
            "assistant_recording",
            "customer_recording",
            "stereo_recording",
            "user_chat_transcript",
            "assistant_chat_transcript",
            "agent_prompt",
        }
        scenario_column_order_set = set(scenario_column_order_list)

        # Build the simulation-context map. These keys are exposed to the
        # eval variable-mapping dropdown in the frontend
        # (SimulationTestMode.jsx) so users can evaluate against agent,
        # persona, prompt, and call metadata — not just transcripts and
        # scenario columns. The keys here MUST stay in sync with what the
        # frontend flattens; when adding a new key, add it in both places.
        context_map = _build_simulation_context_map(call_execution, agent_version)

        # Collect column IDs that need cell lookups vs mismatch lookups
        cell_column_ids = []
        mismatch_column_ids = []
        for value in mapping.values():
            if not value or value == "" or value in known_keys:
                continue
            if value in TRANSCRIPT_DOT_ALIASES:
                # Dot-form alias for transcript_data / snapshot keys.
                continue
            if value in context_map:
                continue
            if value in scenario_column_order_set:
                cell_column_ids.append(value)
            else:
                mismatch_column_ids.append(value)

        # Batch-fetch cells for scenario columns
        cells_by_column = {}
        metadata = call_execution.call_metadata
        row_id = metadata.get("row_id") if metadata else None
        if row_id and cell_column_ids:
            for cell in Cell.objects.filter(
                row=row_id, column__in=cell_column_ids, deleted=False
            ):
                cells_by_column[str(cell.column_id)] = cell.value

        # Batch-fetch columns for mismatch detection
        columns_by_id = {}
        if mismatch_column_ids:
            for col in Column.objects.select_related("dataset").filter(
                id__in=mismatch_column_ids, deleted=False
            ):
                columns_by_id[str(col.id)] = col

        # Batch-fetch scenarios by dataset for mismatch columns
        dataset_ids = [
            col.dataset_id for col in columns_by_id.values() if col.dataset_id
        ]
        scenario_by_dataset = {}
        if dataset_ids:
            for scn in Scenarios.objects.filter(dataset_id__in=dataset_ids):
                scenario_by_dataset[scn.dataset_id] = scn

        # Pre-fetch test scenario names (used in mismatch error messages)
        test_scenarios = list(
            Scenarios.objects.filter(id__in=scenario_ids, deleted=False).values_list(
                "name", flat=True
            )
        )
        test_scenarios_str = (
            ", ".join(test_scenarios) if test_scenarios else "unknown scenarios"
        )

        # Build updated mapping
        updated_mapping = {}
        for key, value in mapping.items():
            if not value or value == "":
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
            elif value == "agent_prompt":
                if agent_version and agent_version.configuration_snapshot:
                    snapshot = agent_version.configuration_snapshot
                    updated_mapping[key] = snapshot.get("description", "")
                else:
                    updated_mapping[key] = ""
            elif value in TRANSCRIPT_DOT_ALIASES:
                # Resolve dot-form aliases back to transcript_data / the
                # agent snapshot. Keeps the branches for the legacy
                # underscore keys above untouched.
                legacy_key = TRANSCRIPT_DOT_ALIASES[value]
                if legacy_key == "agent_prompt":
                    if agent_version and agent_version.configuration_snapshot:
                        snapshot = agent_version.configuration_snapshot
                        updated_mapping[key] = snapshot.get("description", "")
                    else:
                        updated_mapping[key] = ""
                else:
                    updated_mapping[key] = transcript_data.get(legacy_key, "")
            elif value in context_map:
                updated_mapping[key] = context_map[value]
            elif value in scenario_column_order_set:
                if not row_id:
                    updated_mapping[key] = ""
                    continue
                updated_mapping[key] = cells_by_column.get(value, "")
            else:
                # Check if it's a valid column from a different scenario (mismatch)
                column_obj = columns_by_id.get(value)
                column_name = column_obj.name if column_obj else None
                column_scenario_name = None
                if column_obj and column_obj.dataset_id:
                    col_scenario = scenario_by_dataset.get(column_obj.dataset_id)
                    if col_scenario:
                        column_scenario_name = col_scenario.name

                if column_name and column_scenario_name:
                    error_message = (
                        f"Column mapping mismatch: The evaluation '{eval_config.name}' uses column '{column_name}' "
                        f"from scenario '{column_scenario_name}', but the test is running with different scenario(s): [{test_scenarios_str}]. "
                        f"Please reconfigure the evaluation to use columns from the test scenarios."
                    )
                else:
                    error_message = (
                        f"Column mapping mismatch: Column '{value}' is not available in the test scenario(s): [{test_scenarios_str}]. "
                        f"Please reconfigure the evaluation '{eval_config.name}' to use valid columns."
                    )

                logger.warning(
                    f"Error running evaluation {eval_config.id}: Invalid column mapping. "
                    f"Key: '{key}', Value: '{value}'"
                )

                if not call_execution.eval_outputs:
                    call_execution.eval_outputs = {}
                error_result = {
                    "reason": error_message,
                    "error": "error",
                    "name": eval_config.name,
                    "timestamp": timezone.now().isoformat(),
                    "output": None,
                    "output_type": derive_kpi_output_type(eval_template),
                }
                call_execution.eval_outputs[str(eval_config.id)] = error_result
                call_execution.save(update_fields=["eval_outputs"])

                eval_config.status = StatusType.FAILED.value
                eval_config.save()
                raise ValueError(error_message)

        # Prepare config and run evaluation
        config = eval_config.config.copy() if eval_config.config else {}
        organization = call_execution.test_execution.run_test.organization

        from common.utils.data_injection import is_enabled as _di_enabled

        _di_cfg = (
            (config or {}).get("run_config", {}).get("data_injection")
            or (config or {}).get("data_injection")
            or {}
        )

        if eval_template.template_type == "composite":
            _call_context = _build_simulate_call_context(call_execution)
            eval_result = _run_simulate_composite(
                eval_template=eval_template,
                eval_config=eval_config,
                config=config,
                mapping=updated_mapping,
                org=organization,
                workspace=call_execution.test_execution.run_test.workspace,
                call_context=_call_context,
            )
        else:
            _call_context = (
                _build_simulate_call_context(call_execution)
                if _di_enabled(_di_cfg, "call_context")
                else None
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
                call_context=_call_context,
            )

        if isinstance(eval_result, str):
            if (
                "insufficient_credits" in eval_result.lower()
                or "limit reached" in eval_result.lower()
            ):
                raise ValueError(eval_result)
            raise ValueError("Evaluation failed. Please contact Future AGI support.")

        # Store evaluation result
        if eval_result:
            if not call_execution.eval_outputs:
                call_execution.eval_outputs = {}

            eval_output = eval_result.get("output")
            eval_reason = eval_result.get("reason", "")

            call_execution.eval_outputs[str(eval_config.id)] = {
                "output": eval_output,
                "reason": eval_reason,
                "output_type": eval_result.get("output_type"),
                "name": eval_config.name,
            }
            call_execution.save(update_fields=["eval_outputs"])

            # Trigger error localization if enabled
            if eval_config.error_localizer and eval_output is not None:
                try:
                    eval_failed = False
                    if isinstance(eval_output, bool):
                        eval_failed = not eval_output
                    elif isinstance(eval_output, int | float):
                        eval_failed = eval_output < 0.8
                    else:
                        eval_failed = True

                    if eval_failed:
                        trigger_error_localization_for_simulate(
                            eval_template=eval_template,
                            call_execution=call_execution,
                            eval_config=eval_config,
                            value=eval_output,
                            mapping=updated_mapping,
                            eval_explanation=eval_reason,
                            log_id=None,
                        )
                except Exception as e:
                    logger.error(
                        f"Error triggering error localization for evaluation {eval_config.id}: {str(e)}"
                    )

            eval_config.status = StatusType.COMPLETED.value
            eval_config.save()

    except Exception as e:
        logger.error(f"Error running evaluation {eval_config.id}: {str(e)}")

        if not call_execution.eval_outputs:
            call_execution.eval_outputs = {}

        error_result = {
            "reason": get_specific_error_message(e),
            "error": "error",
            "name": eval_config.name,
            "timestamp": timezone.now().isoformat(),
            "output": None,
            "output_type": derive_kpi_output_type(eval_config.eval_template),
        }
        call_execution.eval_outputs[str(eval_config.id)] = error_result
        call_execution.save(update_fields=["eval_outputs"])

        eval_config.status = StatusType.FAILED.value
        eval_config.save()
        raise


def _build_simulate_call_context(call_execution) -> dict:
    """Build the ``call_context`` payload from a ``CallExecution`` row."""
    return {
        "id": str(call_execution.id),
        "status": call_execution.status,
        "call_type": call_execution.call_type,
        "simulation_call_type": call_execution.simulation_call_type,
        "phone_number": call_execution.phone_number,
        "started_at": str(call_execution.started_at) if call_execution.started_at else None,
        "ended_at": str(call_execution.ended_at) if call_execution.ended_at else None,
        "duration_seconds": call_execution.duration_seconds,
        "recording_url": call_execution.recording_url,
        "call_summary": call_execution.call_summary,
        "ended_reason": call_execution.ended_reason,
        "error_message": call_execution.error_message,
        "message_count": call_execution.message_count,
        "overall_score": float(call_execution.overall_score) if call_execution.overall_score is not None else None,
    }


def _run_simulate_composite(
    *,
    eval_template,
    eval_config,
    config,
    mapping,
    org,
    workspace,
    call_context,
):
    """Fan out a composite eval against a simulate call; return a single-eval-shaped dict."""
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    child_links = list(
        CompositeEvalChild.objects.filter(parent=eval_template, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(
            f"Composite eval template '{eval_template.name}' has no children."
        )

    outcome = execute_composite_children_sync(
        parent=eval_template,
        child_links=child_links,
        mapping=mapping,
        config=config or {},
        org=org,
        workspace=workspace,
        model=eval_config.model,
        error_localizer=eval_config.error_localizer,
        source="simulate_composite",
        call_context=call_context,
    )

    if eval_template.aggregation_enabled:
        output = outcome.aggregate_score
        output_type = "score"
    else:
        output = outcome.summary or ""
        output_type = "text"

    return {
        "output": output,
        "reason": outcome.summary or "",
        "output_type": output_type,
        "metadata": {
            "composite_id": str(eval_template.id),
            "aggregation_enabled": eval_template.aggregation_enabled,
            "aggregation_function": eval_template.aggregation_function,
            "aggregate_pass": outcome.aggregate_pass,
            "children": [cr.model_dump() for cr in outcome.child_results],
        },
    }


def _check_eval_completion(call_execution, eval_config_ids=None, run_test=None):
    """
    Check if all expected eval configs have completed and set eval_completed flag.

    Replicates TestExecutor._check_and_update_eval_completion() logic,
    but does NOT update test_execution status (workflow/rerun handles that).
    """
    from simulate.models import SimulateEvalConfig

    try:
        call_execution.refresh_from_db()

        if not run_test:
            run_test = call_execution.test_execution.run_test

        if eval_config_ids:
            expected_eval_configs = SimulateEvalConfig.objects.filter(
                id__in=eval_config_ids, deleted=False
            )
        else:
            expected_eval_configs = SimulateEvalConfig.objects.filter(
                run_test=run_test, deleted=False
            )

        if not expected_eval_configs.exists():
            if not call_execution.call_metadata:
                call_execution.call_metadata = {}
            call_execution.call_metadata["eval_completed"] = True
            call_execution.save(update_fields=["call_metadata"])
            return

        # Check if all expected eval configs have completed results
        eval_outputs = call_execution.eval_outputs or {}
        all_configs_completed = True

        for eval_config in expected_eval_configs:
            eval_config_id_str = str(eval_config.id)
            eval_result = eval_outputs.get(eval_config_id_str)
            if not eval_result or eval_result.get("status") == "pending":
                all_configs_completed = False
                break

        if all_configs_completed:
            try:
                with transaction.atomic():
                    call_execution_locked = (
                        CallExecution.objects.select_for_update().get(
                            id=call_execution.id
                        )
                    )
                    if not call_execution_locked.call_metadata.get(
                        "eval_completed", False
                    ):
                        eval_outputs_locked = call_execution_locked.eval_outputs or {}
                        all_configs_still_completed = all(
                            eval_outputs_locked.get(str(ec.id)) is not None
                            and eval_outputs_locked.get(str(ec.id), {}).get("status")
                            != "pending"
                            for ec in expected_eval_configs
                        )
                        if all_configs_still_completed:
                            if not call_execution_locked.call_metadata:
                                call_execution_locked.call_metadata = {}
                            call_execution_locked.call_metadata["eval_completed"] = True
                            call_execution_locked.save(update_fields=["call_metadata"])
                            logger.info(
                                f"All evaluations completed for call {call_execution_locked.id} "
                                f"({len(expected_eval_configs)} eval configs)"
                            )
            except Exception as e:
                # Fallback to regular update if select_for_update fails
                logger.warning(
                    f"Could not use select_for_update for call {call_execution.id}, "
                    f"falling back to regular update: {str(e)}"
                )
                call_execution.refresh_from_db()
                if not call_execution.call_metadata.get("eval_completed", False):
                    eval_outputs_final = call_execution.eval_outputs or {}
                    all_still_completed = all(
                        eval_outputs_final.get(str(ec.id)) is not None
                        and eval_outputs_final.get(str(ec.id), {}).get("status")
                        != "pending"
                        for ec in expected_eval_configs
                    )
                    if all_still_completed:
                        if not call_execution.call_metadata:
                            call_execution.call_metadata = {}
                        call_execution.call_metadata["eval_completed"] = True
                        call_execution.save(update_fields=["call_metadata"])
                        logger.info(
                            f"All evaluations completed for call {call_execution.id} "
                            f"({len(expected_eval_configs)} eval configs) - fallback path"
                        )

    except Exception as e:
        logger.error(
            f"Error checking eval completion for call {call_execution.id}: {str(e)}"
        )
        traceback.print_exc()


def _run_evaluations_standalone(
    call_execution, eval_config_ids=None, skip_existing=False
):
    """
    Standalone evaluation orchestrator — replaces TestExecutor._run_simulate_evaluations().

    Runs all configured SimulateEvalConfig evaluations for a call execution.
    Does NOT run tool evaluation (handled by separate activity).
    Does NOT update test_execution status (workflow/rerun handles that).
    """
    from simulate.models import SimulateEvalConfig, TestExecution

    try:
        close_old_connections()
        run_test = call_execution.test_execution.run_test

        # Refresh from DB to get latest data
        call_execution.refresh_from_db()

        # Mark evaluations as started
        if not call_execution.call_metadata:
            call_execution.call_metadata = {}
        call_execution.call_metadata["eval_started"] = True
        call_execution.save(update_fields=["call_metadata"])
        logger.info(f"Starting evaluations for call {call_execution.id}")

        # Get eval configs
        if eval_config_ids:
            eval_configs = SimulateEvalConfig.objects.filter(
                id__in=eval_config_ids, deleted=False
            ).select_related("eval_template")
        else:
            eval_configs = SimulateEvalConfig.objects.filter(
                run_test=run_test, deleted=False
            ).select_related("eval_template")

        if not eval_configs.exists():
            logger.info(f"No evaluation configs found for run test {run_test.id}")
            if not call_execution.call_metadata:
                call_execution.call_metadata = {}
            call_execution.call_metadata["eval_completed"] = True
            call_execution.save(update_fields=["call_metadata"])
            return

        # Build transcript data from DB
        transcript_data = _build_transcript_data(call_execution)

        # Handle no transcript
        if not transcript_data["transcript"]:
            logger.info(
                f"No transcript data available for call execution {call_execution.id}, skipping evaluations"
            )
            if not call_execution.eval_outputs:
                call_execution.eval_outputs = {}
            for eval_config in eval_configs:
                call_execution.eval_outputs[str(eval_config.id)] = {
                    "output": None,
                    "reason": "No transcript data available",
                    "output_type": derive_kpi_output_type(eval_config.eval_template),
                    "name": eval_config.name,
                }
            if not call_execution.call_metadata:
                call_execution.call_metadata = {}
            call_execution.call_metadata["eval_completed"] = True
            call_execution.save(update_fields=["call_metadata", "eval_outputs"])
            return

        # Run each evaluation
        for eval_config in eval_configs:
            try:
                if (
                    call_execution.eval_outputs
                    and str(eval_config.id) in call_execution.eval_outputs
                ):
                    logger.info(
                        f"Evaluation {eval_config.id} already exists for call {call_execution.id}, "
                        f"it will be overwritten"
                    )

                test_execution = TestExecution.objects.filter(
                    id=call_execution.test_execution_id
                ).get()
                if test_execution.status not in [
                    TestExecution.ExecutionStatus.CANCELLED,
                    TestExecution.ExecutionStatus.CANCELLING,
                ]:
                    _run_single_evaluation(eval_config, call_execution, transcript_data)
                    logger.info(
                        f"Successfully ran evaluation {eval_config.name} ({eval_config.id}) "
                        f"on call execution {call_execution.id}"
                    )
            except Exception as e:
                logger.error(f"Error running evaluation {eval_config.id}: {str(e)}")
                traceback.print_exc()

        # Check completion — sets call_metadata.eval_completed = True
        _check_eval_completion(
            call_execution,
            eval_config_ids=eval_config_ids,
            run_test=run_test,
        )

    except Exception as e:
        # On error, still try to check completion in case some configs completed
        try:
            _check_eval_completion(
                call_execution,
                eval_config_ids=eval_config_ids,
            )
        except Exception:
            pass
        logger.error(f"Error in _run_evaluations_standalone: {str(e)}")
        traceback.print_exc()


# ============================================================================
# TOOL CALL EVALUATION ACTIVITY
# ============================================================================


@activity.defn(name="run_tool_call_evaluation")
async def run_tool_call_evaluation(
    input: RunToolCallEvaluationInput,
) -> RunToolCallEvaluationOutput:
    """
    Standalone tool evaluation activity.

    Evaluates tool calls made during a call execution using ToolEvalAgent.
    Checks enable_tool_evaluation flag internally and skips if disabled.
    Uses customer_call_id already set by fetch_client_call_data (Phase 5).

    Timeout: up to 3 hours (with heartbeats every ~30 seconds)
    Queue: tasks_xl
    """
    close_old_connections()

    from tfc.temporal.common.heartbeat import Heartbeater

    async with Heartbeater(factor=4) as heartbeater:
        heartbeater.details = (
            f"running tool evaluation for {input.call_execution_id}",
        )

        try:
            activity.logger.info(
                f"Running tool call evaluation for call execution {input.call_execution_id}"
            )

            # Check for cancellation before starting
            if activity.is_cancelled():
                raise asyncio.CancelledError("Activity cancelled before starting")

            # Fetch call execution with related objects
            heartbeater.details = (
                "fetching_call_execution",
                input.call_execution_id,
            )
            call_execution = await CallExecution.objects.select_related(
                "test_execution",
                "test_execution__agent_definition",
                "test_execution__run_test",
                "test_execution__run_test__organization",
                "test_execution__run_test__workspace",
                "test_execution__run_test__agent_definition",
            ).aget(id=input.call_execution_id)

            test_execution = call_execution.test_execution

            # Check if tool evaluation is enabled
            if not test_execution.run_test.enable_tool_evaluation:
                activity.logger.info(
                    f"Tool evaluation disabled for run test {test_execution.run_test.id}, skipping"
                )
                return RunToolCallEvaluationOutput(success=True)

            # Run tool evaluation in sync context
            heartbeater.details = (
                "running_tool_evaluation",
                input.call_execution_id,
            )

            def run_tool_eval_sync():
                _run_tool_evaluation_standalone(call_execution, test_execution)

            await sync_to_async(run_tool_eval_sync, thread_sensitive=False)()

            activity.logger.info(
                f"Successfully completed tool call evaluation for call execution {input.call_execution_id}"
            )
            return RunToolCallEvaluationOutput(success=True)

        except asyncio.CancelledError:
            activity.logger.info(
                f"Tool evaluation cancelled for call execution {input.call_execution_id}"
            )
            raise

        except CallExecution.DoesNotExist:
            activity.logger.error(
                f"Call execution not found for tool evaluation: {input.call_execution_id}"
            )
            return RunToolCallEvaluationOutput(
                success=False,
                error=f"Call execution not found: {input.call_execution_id}",
            )

        except Exception as e:
            activity.logger.error(
                f"Error running tool call evaluation for call execution {input.call_execution_id}: {str(e)}"
            )
            activity.logger.exception(
                f"Error running tool call evaluation for call execution {input.call_execution_id}: {str(e)}"
            )
            return RunToolCallEvaluationOutput(
                success=False,
                error=str(e),
            )

        finally:
            close_old_connections()


async def _inject_client_tool_calls(call: CallExecution, client_raw_data: dict) -> None:
    """Extract tool_calls / tool_call_result from client's provider data
    and insert them into CallTranscript so the full conversation
    (including customer's tool calls) is visible in our transcript.

    Uses secondsFromStart (converted to ms) so timestamps align with
    the LiveKit agent worker's offset-from-call-start convention.
    """
    import json

    from simulate.models.test_execution import CallTranscript

    artifact = client_raw_data.get("artifact", {})
    messages = artifact.get("messages", [])
    if not messages:
        return

    transcript_records = []

    for message in messages:
        role = message.get("role", "")

        if role == "tool_calls" and message.get("toolCalls"):
            # Extract tool name(s) for display in transcript UI
            tool_names = [
                tc.get("function", {}).get("name", "unknown")
                for tc in message.get("toolCalls", [])
            ]
            content = ", ".join(tool_names)
            start_ms = int(message.get("secondsFromStart", 0) * 1000)

            transcript_records.append(
                CallTranscript(
                    call_execution=call,
                    speaker_role=CallTranscript.SpeakerRole.TOOL_CALLS,
                    content=content,
                    start_time_ms=start_ms,
                    end_time_ms=start_ms,
                )
            )

        elif role == "tool_call_result":
            result = message.get("result", "")
            if not isinstance(result, str):
                result = json.dumps(result)
            start_ms = int(message.get("secondsFromStart", 0) * 1000)

            transcript_records.append(
                CallTranscript(
                    call_execution=call,
                    speaker_role=CallTranscript.SpeakerRole.TOOL_CALL_RESULT,
                    content=result,
                    start_time_ms=start_ms,
                    end_time_ms=start_ms,
                )
            )

    if transcript_records:
        await CallTranscript.objects.abulk_create(transcript_records)
        activity.logger.info(
            f"Injected {len(transcript_records)} tool call transcript(s) "
            f"for call_id={call.id}"
        )


def _run_tool_evaluation_standalone(call_execution, test_execution):
    """
    Standalone tool evaluation — replaces TestExecutor._run_tool_evaluation().

    Uses customer_call_id already set on CallExecution by fetch_client_call_data (Phase 5).
    Does NOT call find_client_call_id again.
    """
    try:
        from ee.agenthub.tool_eval_agent.tool_eval_agent import ToolEvalAgent
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.tool_eval_agent.tool_eval_agent", exc_info=True)
        return
    from model_hub.models.choices import EvalOutputType
    from sdk.utils.helpers import _get_api_call_type
    from simulate.models import AgentDefinition
    from tfc.utils.error_codes import get_specific_error_message
    from tracer.models.observability_provider import ProviderChoices
    try:
        from ee.usage.models.usage import APICallStatusChoices
    except ImportError:
        APICallStatusChoices = None
    try:
        from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
    except ImportError:
        log_and_deduct_cost_for_api_request = None

    try:
        # Skip if no service_provider_call_id for VOICE agents
        if (
            call_execution.simulation_call_type != CallExecution.SimulationCallType.TEXT
            and not call_execution.service_provider_call_id
        ):
            logger.info(
                f"Skipping tool evaluation for call {call_execution.id} - no service_provider_call_id"
            )
            return

        logger.info(f"Running tool evaluation for call execution {call_execution.id}")

        agent = ToolEvalAgent()

        # Get agent version and snapshot — prefer test_execution.agent_definition
        agent_definition = test_execution.agent_definition
        if not agent_definition:
            agent_definition = test_execution.run_test.agent_definition
        selected_version = test_execution.agent_version
        agent_version = None
        if not selected_version:
            agent_version = agent_definition.latest_version
        else:
            agent_version = agent_definition.get_version(selected_version.id)

        snapshot = agent_version.configuration_snapshot
        agent_type = agent_definition.agent_type
        is_text_agent = agent_type == AgentDefinition.AgentTypeChoices.TEXT

        if is_text_agent:
            logger.info("Processing TEXT agent - using chat session data")
            try:
                from ee.agenthub.tool_eval_agent.adapters import (
                    ChatToolCallAdapter,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.tool_eval_agent.adapters", exc_info=True)
                return

            try:
                adapter = ChatToolCallAdapter()
                messages = adapter.get_tool_call_transcript(call_execution)
            except Exception as e:
                logger.error(f"Error fetching chat data from database: {str(e)}")
                return
        else:
            # VOICE agent — use customer_call_id already set by Phase 5
            try:
                from ee.agenthub.tool_eval_agent.adapters import (
                    get_tool_call_adapter,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.tool_eval_agent.adapters", exc_info=True)
                return

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

            if not customer_api_key or not customer_assistant_id:
                logger.info(
                    "No customer credentials provided, skipping tool evaluation"
                )
                return

            # Use customer_call_id already set by fetch_client_call_data (Phase 5)
            customer_call_id = call_execution.customer_call_id
            if not customer_call_id:
                logger.warning(
                    f"No customer_call_id found for call {call_execution.id}, skipping tool evaluation"
                )
                return

            logger.info(f"Using customer_call_id: {customer_call_id}")

            customer_provider = snapshot.get("provider", ProviderChoices.VAPI)
            adapter = get_tool_call_adapter(customer_provider)
            messages = adapter.get_tool_call_transcript(
                call_execution=call_execution,
                customer_call_id=customer_call_id,
                api_key=customer_api_key,
            )

        # Unified path: extract tool calls + context from normalized messages
        extracted = agent.evaluate_tool_calls(messages)
        tool_calls_data = extracted["tool_calls_data"]
        conversation_context = extracted["conversation_context"]

        if not tool_calls_data:
            # No tool calls — store empty tool_column_order to track processing
            if not call_execution.evaluation_data:
                call_execution.evaluation_data = {}
            call_execution.evaluation_data["tool_column_order"] = []
            call_execution.save(update_fields=["evaluation_data"])
            return

        # Initialize tool_outputs
        if not call_execution.tool_outputs:
            call_execution.tool_outputs = {}
        else:
            logger.info(
                f"Tool outputs already exist for call execution {call_execution.id}, skipping"
            )
            return

        # PHASE 1: Build column_order
        if not test_execution.execution_metadata:
            test_execution.execution_metadata = {}

        test_column_order = list(
            test_execution.execution_metadata.get("column_order", [])
        )
        # Build lookup for existing tool_evaluation columns by name.
        # Handle both new snake_case and legacy camelCase stored records.
        existing_tool_cols = {
            (col.get("column_name") or col.get("columnName")): col
            for col in test_column_order
            if col.get("type") == "tool_evaluation"
            and (col.get("column_name") or col.get("columnName"))
        }
        call_column_order = []
        tool_eval_ids_map = {}
        columns_updated = False
        tool_name_counts = {}

        for idx, tool_call in enumerate(tool_calls_data):
            try:
                tool_name = tool_call.get("tool_name", "Unknown")
                tool_call_id = tool_call.get("tool_call_id", f"unknown_{idx}")

                tool_occurrence = tool_name_counts.get(tool_name, 0) + 1
                tool_name_counts[tool_name] = tool_occurrence
                column_name = f"{tool_name} #{tool_occurrence}"

                # Check if column already exists in test_execution
                existing_col = existing_tool_cols.get(column_name)
                tool_eval_id = existing_col["id"] if existing_col else None

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

                call_column_order.append(column_def)

                tool_eval_ids_map[idx] = {
                    "tool_eval_id": tool_eval_id,
                    "column_name": column_name,
                    "tool_name": tool_name,
                    "tool_call": tool_call,
                }

                call_execution.tool_outputs[tool_eval_id] = {
                    "value": "",
                    "reason": "",
                    "type": EvalOutputType.PASS_FAIL.value,
                    "name": column_name,
                    "error": False,
                    "status": "running",
                }

            except Exception as e:
                logger.error(
                    f"Error initializing tool call #{idx + 1} for call {call_execution.id}: {str(e)}"
                )
                traceback.print_exc()

        if columns_updated:
            test_execution.execution_metadata["column_order"] = test_column_order
            test_execution.save(update_fields=["execution_metadata"])

        if not call_execution.evaluation_data:
            call_execution.evaluation_data = {}
        call_execution.evaluation_data["tool_column_order"] = call_column_order
        call_execution.save(update_fields=["tool_outputs", "evaluation_data"])

        # PHASE 2: Evaluate each tool call
        for idx, tool_eval_info in tool_eval_ids_map.items():
            try:
                tool_eval_id = tool_eval_info["tool_eval_id"]
                column_name = tool_eval_info["column_name"]
                tool_name = tool_eval_info["tool_name"]
                tool_call = tool_eval_info["tool_call"]

                organization = test_execution.run_test.organization
                workspace = test_execution.run_test.workspace

                model = agent.llm.model_name if hasattr(agent, "llm") else None

                source_config = {
                    "source": "simulate_tool_evaluation",
                    "test_execution_id": str(test_execution.id),
                    "call_execution_id": str(call_execution.id),
                    "tool_name": tool_name,
                    "tool_call_id": tool_call.get("tool_call_id", f"unknown_{idx}"),
                    "tool_index": idx + 1,
                }
                if model:
                    source_config["model"] = model

                api_call_type = _get_api_call_type(model=None)

                try:
                    from ee.usage.services.metering import check_usage
                except ImportError:
                    check_usage = None

                usage_check = check_usage(str(organization.id), api_call_type)
                if not usage_check.allowed:
                    raise ValueError(usage_check.reason or "Usage limit exceeded")

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
                        "type": EvalOutputType.PASS_FAIL.value,
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
                        "type": EvalOutputType.PASS_FAIL.value,
                        "name": column_name,
                        "error": True,
                        "status": "failed",
                    }
                    continue

                evaluation = agent._evaluate_single_tool_call(
                    tool_call=tool_call,
                    conversation_context=conversation_context,
                    all_tool_calls=tool_calls_data,
                )

                call_execution.tool_outputs[tool_eval_id] = {
                    "value": evaluation.get("result", "Failed"),
                    "reason": evaluation.get("summary", ""),
                    "type": EvalOutputType.PASS_FAIL.value,
                    "name": column_name,
                    "error": False,
                    "status": "completed",
                }

                result_status = "PASSED" if evaluation.get("result") else "FAILED"
                logger.info(
                    f"Successfully evaluated {column_name} for call {call_execution.id}: {result_status}"
                )

                # Dual-write: emit cost-based usage event
                try:
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.config import BillingConfig
                    except ImportError:
                        BillingConfig = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

                    actual_cost = 0
                    if hasattr(agent, "llm") and agent.llm:
                        actual_cost = getattr(agent.llm, "cost", {}).get(
                            "total_cost", 0
                        )
                    credits = BillingConfig.get().calculate_ai_credits(actual_cost)

                    emit(
                        UsageEvent(
                            org_id=str(organization.id),
                            event_type=api_call_type,
                            amount=credits,
                            properties={
                                "source": "simulate_tool_evaluation",
                                "source_id": str(test_execution.id),
                                "raw_cost_usd": str(actual_cost),
                            },
                        )
                    )
                except Exception:
                    pass  # Metering failure must not break the action

            except Exception as e:
                logger.error(
                    f"Error evaluating tool call #{idx + 1} for call {call_execution.id}: {str(e)}"
                )
                traceback.print_exc()

                if idx in tool_eval_ids_map:
                    tool_eval_id = tool_eval_ids_map[idx]["tool_eval_id"]
                    tool_name = tool_eval_ids_map[idx]["tool_name"]
                    call_execution.tool_outputs[tool_eval_id] = {
                        "value": "",
                        "reason": get_specific_error_message(str(e)),
                        "type": EvalOutputType.PASS_FAIL.value,
                        "name": f"{tool_name} #{idx + 1}",
                        "error": True,
                        "status": "failed",
                    }

        # Save all results
        call_execution.save(update_fields=["tool_outputs"])
        logger.info(
            f"Successfully completed tool evaluation for call {call_execution.id} "
            f"- evaluated {len(tool_calls_data)} tool call(s)"
        )

    except Exception as e:
        logger.error(
            f"Error in _run_tool_evaluation_standalone for call {call_execution.id}: {str(e)}"
        )
        traceback.print_exc()
