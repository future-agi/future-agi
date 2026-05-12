import traceback
from datetime import datetime

import structlog
from django.db.models import Count, Q
from rest_framework import serializers

from model_hub.models.develop_dataset import Cell, Column, Row
from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
    CallTranscript,
    TestExecution,
)
from simulate.serializers.chat_message import ChatMessageSerializer

try:
    from ee.voice.services.voice_service_manager import VoiceServiceManager
except ImportError:
    VoiceServiceManager = None
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


class CallTranscriptSerializer(serializers.ModelSerializer):
    """Serializer for CallTranscript model"""

    start_time_seconds = serializers.SerializerMethodField()
    end_time_seconds = serializers.SerializerMethodField()

    class Meta:
        model = CallTranscript
        fields = [
            "id",
            "speaker_role",
            "content",
            "start_time_ms",
            "start_time_seconds",
            "end_time_ms",
            "end_time_seconds",
            "confidence_score",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_start_time_seconds(self, obj):
        """Convert start_time_ms to seconds, aligned to recording t=0."""
        if obj.start_time_ms is not None:
            offset = self.context.get("recording_offset_ms", 0)
            return round((obj.start_time_ms + offset) / 1000, 3)
        return None

    def get_end_time_seconds(self, obj):
        """Convert end_time_ms to seconds, aligned to recording t=0."""
        if obj.end_time_ms is not None and obj.end_time_ms > 0:
            offset = self.context.get("recording_offset_ms", 0)
            return round((obj.end_time_ms + offset) / 1000, 3)
        return None


class CallExecutionSnapshotSerializer(serializers.ModelSerializer):
    """Serializer for CallExecutionSnapshot model"""

    # Return customer_call_id from parent CallExecution for frontend compatibility
    service_provider_call_id = serializers.SerializerMethodField()

    def get_service_provider_call_id(self, obj):
        """Get customer_call_id from the parent CallExecution"""
        if obj.call_execution:
            return obj.call_execution.customer_call_id
        return None

    class Meta:
        model = CallExecutionSnapshot
        fields = [
            "id",
            "snapshot_timestamp",
            "rerun_type",
            "service_provider_call_id",
            "status",
            "started_at",
            "completed_at",
            "ended_at",
            "duration_seconds",
            "recording_url",
            "stereo_recording_url",
            "cost_cents",
            "stt_cost_cents",
            "llm_cost_cents",
            "tts_cost_cents",
            "call_summary",
            "ended_reason",
            "overall_score",
            "response_time_ms",
            "assistant_id",
            "customer_number",
            "call_type",
            "message_count",
            "transcript_available",
            "recording_available",
            "eval_outputs",
            "transcripts",
            "analysis_data",
            "evaluation_data",
            "provider_call_data",
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
        ]
        read_only_fields = ["id", "snapshot_timestamp"]


def _normalize_eval_value(value, output_type):
    """Normalize evaluation result values for the frontend.

    For ``output_type == "choices"`` the stored value is the LLM's single
    selected category (a string). The run-detail grid renders choice cells as
    a list of Chip components and expects an iterable, so wrap bare scalars in
    a single-element list. Values that are already lists, None, or for other
    output types are returned unchanged.
    """
    if output_type == "choices" and value is not None and not isinstance(value, list):
        return [value]
    return value


class CallExecutionDetailSerializer(serializers.ModelSerializer):
    """Serializer for CallExecution model with new payload structure"""

    timestamp = serializers.DateTimeField(source="updated_at", read_only=True)
    call_type = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    start_time = serializers.SerializerMethodField()
    transcript = serializers.SerializerMethodField()
    scenario = serializers.CharField(source="scenario.name", read_only=True)
    overall_score = serializers.SerializerMethodField()
    response_time = serializers.SerializerMethodField()
    audio_url = serializers.URLField(source="recording_url", read_only=True)
    recordings = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="customer_number", read_only=True)
    eval_outputs = serializers.SerializerMethodField()
    session_id = serializers.SerializerMethodField()
    # Return customer_call_id as service_provider_call_id for frontend compatibility
    service_provider_call_id = serializers.CharField(
        source="customer_call_id", read_only=True
    )

    # New fields for simulator and agent definition used in this execution
    simulator_agent_name = serializers.CharField(
        source="test_execution.simulator_agent.name", read_only=True
    )
    simulator_agent_id = serializers.UUIDField(
        source="test_execution.simulator_agent.id", read_only=True
    )
    agent_definition_used_name = serializers.CharField(
        source="test_execution.agent_definition.agent_name", read_only=True
    )
    agent_definition_used_id = serializers.UUIDField(
        source="test_execution.agent_definition.id", read_only=True
    )

    # Dynamic evaluation metrics - these will be populated based on eval_configs
    eval_metrics = serializers.SerializerMethodField()

    # Dynamic scenario columns - these will be populated based on scenarios
    scenario_columns = serializers.SerializerMethodField()

    # Graph-related field
    scenario_id = serializers.SerializerMethodField()

    # Provider used for this call (e.g. "vapi", "retell", "livekit_bridge")
    provider = serializers.SerializerMethodField()

    # Conversation metrics fields
    avg_agent_latency = serializers.IntegerField(
        source="avg_agent_latency_ms", read_only=True
    )
    avg_stop_time_after_interruption = serializers.IntegerField(
        source="avg_stop_time_after_interruption_ms", read_only=True
    )

    # Chat metrics fields (from conversation_metrics_data)
    total_tokens = serializers.SerializerMethodField()
    input_tokens = serializers.SerializerMethodField()
    output_tokens = serializers.SerializerMethodField()
    avg_latency_ms = serializers.SerializerMethodField()
    turn_count = serializers.SerializerMethodField()
    agent_talk_percentage = serializers.SerializerMethodField()
    csat_score = serializers.SerializerMethodField()
    processing_skipped = serializers.SerializerMethodField()
    processing_skip_reason = serializers.SerializerMethodField()

    # Rerun snapshots field - only show call_and_eval reruns
    rerun_snapshots = serializers.SerializerMethodField()

    # Snapshot metadata fields (for when this is a snapshot)
    is_snapshot = serializers.SerializerMethodField()
    snapshot_timestamp = serializers.SerializerMethodField()
    rerun_type = serializers.SerializerMethodField()
    original_call_execution_id = serializers.SerializerMethodField()

    class Meta:
        model = CallExecution
        fields = [
            "id",
            "service_provider_call_id",
            "session_id",
            "timestamp",
            "call_type",
            "status",
            "duration",
            "start_time",
            "transcript",
            "scenario",
            "overall_score",
            "response_time",
            "audio_url",
            "customer_name",
            "eval_outputs",
            "eval_metrics",
            "scenario_columns",
            "ended_reason",
            "simulator_agent_name",
            "simulator_agent_id",
            "agent_definition_used_name",
            "agent_definition_used_id",
            "call_summary",
            "recordings",
            "scenario_id",
            # Conversation metrics fields
            "avg_agent_latency",
            "user_interruption_count",
            "user_interruption_rate",
            "user_wpm",
            "bot_wpm",
            "talk_ratio",
            "ai_interruption_count",
            "ai_interruption_rate",
            "avg_stop_time_after_interruption",
            # Chat metrics fields
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "avg_latency_ms",
            "turn_count",
            "agent_talk_percentage",
            "csat_score",
            "processing_skipped",
            "processing_skip_reason",
            # Rerun snapshots
            "rerun_snapshots",
            # Snapshot metadata fields
            "is_snapshot",
            "snapshot_timestamp",
            "rerun_type",
            "original_call_execution_id",
            "tool_outputs",
            "customer_cost_cents",
            "customer_cost_breakdown",
            "customer_latency_metrics",
            "customer_call_id",
            "simulation_call_type",
            "provider",
            "phone_number",
        ]
        read_only_fields = ["id", "timestamp"]

    def get_session_id(self, obj):
        """
        Return session_id (if present) from the dataset Row.metadata for this call execution.

        Optimized: expects a precomputed mapping in serializer context:
        - context["row_session_id_map"] = { "<row_uuid>": "<session_id>" }
        """
        row_session_id_map = (
            self.context.get("row_session_id_map", {})
            if hasattr(self, "context") and self.context
            else {}
        )

        # Model instances
        row_id = getattr(obj, "row_id", None)
        if (
            not row_id
            and hasattr(obj, "call_metadata")
            and isinstance(obj.call_metadata, dict)
        ):
            row_id = obj.call_metadata.get("row_id")
        return row_session_id_map.get(str(row_id)) if row_id else None

    def get_recordings(self, obj):
        """Return provider recording URLs from stored provider payload (no external API calls).
        Skipped when detail_mode=False (list view) to reduce response size."""
        if not self.context.get("detail_mode", True):
            return {}

        provider_payload = None
        if hasattr(obj, "provider_call_data") and isinstance(
            obj.provider_call_data, dict
        ):
            if len(obj.provider_call_data.keys()) == 1:
                provider_payload = next(iter(obj.provider_call_data.values()))
            else:
                # Prefer VAPI payload when present (current tooling support)
                provider_payload = obj.provider_call_data.get(
                    ProviderChoices.VAPI.value
                )

        if provider_payload:
            if provider_payload.get("recording"):
                return provider_payload.get("recording")

        if VoiceServiceManager is None:
            return {}
        vsm = VoiceServiceManager(system_voice_provider=ProviderChoices.VAPI)
        return vsm.get_recording_urls(provider_payload) or {}

    def get_provider(self, obj):
        """Return the provider that produced this call's stored provider payload.

        The agent definition can be Vapi while the executed call payload is
        stored under another provider key (for example LiveKit web/SIP flows).
        The drawer uses this value for both the chip and provider-specific
        metrics rendering, so prefer the actual payload key when available.
        """
        provider_data = getattr(obj, "provider_call_data", None)
        if isinstance(provider_data, dict):
            for provider in (
                ProviderChoices.VAPI.value,
                ProviderChoices.RETELL.value,
                ProviderChoices.LIVEKIT.value,
                ProviderChoices.ELEVEN_LABS.value,
                ProviderChoices.OTHERS.value,
            ):
                if isinstance(provider_data.get(provider), dict) and provider_data.get(
                    provider
                ):
                    return provider

            for provider, payload in provider_data.items():
                if isinstance(payload, dict) and payload:
                    return provider

        try:
            return obj.test_execution.agent_definition.provider
        except (AttributeError, Exception):
            return None

    def get_call_type(self, obj):
        """
        Determines the call type ('Inbound' or 'Outbound') from the call_metadata.
        """
        INBOUND = "Inbound"
        OUTBOUND = "Outbound"

        if hasattr(obj, "call_metadata") and isinstance(obj.call_metadata, dict):
            call_direction = obj.call_metadata.get("call_direction")
            if call_direction == "outbound":
                return OUTBOUND
            elif call_direction == "inbound":
                return INBOUND

        return INBOUND

    def _get_chat_messages_prefetch_aware(self, obj):
        """
        Return chat messages for this call execution, using prefetched data when available.
        """
        if (
            hasattr(obj, "_prefetched_objects_cache")
            and "chat_messages" in obj._prefetched_objects_cache
        ):
            return list(obj._prefetched_objects_cache["chat_messages"])
        return list(obj.chat_messages.all().order_by("created_at"))

    def _get_first_last_chat_message_times(self, obj):
        """
        Return (first_created_at, last_created_at) for chat messages on this call execution.
        """
        chat_messages = self._get_chat_messages_prefetch_aware(obj)
        if not chat_messages:
            return None, None

        chat_messages_sorted = sorted(
            chat_messages,
            key=lambda m: m.created_at if m.created_at else datetime.min,
        )
        first_msg = chat_messages_sorted[0] if chat_messages_sorted else None
        last_msg = chat_messages_sorted[-1] if chat_messages_sorted else None
        first_time = (
            first_msg.created_at if first_msg and first_msg.created_at else None
        )
        last_time = last_msg.created_at if last_msg and last_msg.created_at else None
        return first_time, last_time

    def _is_chat_simulation(self, obj):
        """Check if this call execution is a chat/text simulation (agent-based or prompt-based)."""
        if not hasattr(obj, "test_execution") or not obj.test_execution:
            return False
        run_test = obj.test_execution.run_test
        agent_definition = run_test.agent_definition
        agent_type = agent_definition.agent_type if agent_definition else None
        return agent_type == "text" or run_test.source_type == "prompt"

    def get_duration(self, obj):
        """Calculate duration in seconds from started_at and completed_at, or from chat messages for chat agents"""
        # Handle grouped/flattened data
        if isinstance(obj, dict):
            duration = obj.get("duration_seconds")
            return round(duration, 2) if duration is not None else None

        # For chat/prompt-based agents, calculate duration from chat messages (last msg time - first msg time)
        if self._is_chat_simulation(obj):
            first_time, last_time = self._get_first_last_chat_message_times(obj)
            if first_time and last_time:
                duration_delta = last_time - first_time
                return round(duration_delta.total_seconds(), 2)
            return None

        # For voice agents, use duration_seconds field
        if hasattr(obj, "duration_seconds"):
            duration = obj.duration_seconds
            return round(duration, 2) if duration is not None else None
        return None

    def get_start_time(self, obj):
        """Get start time from started_at or first chat message for chat agents"""
        # Handle grouped/flattened data
        if isinstance(obj, dict):
            return obj.get("started_at")

        # For chat/prompt-based agents, use first chat message's created_at
        if self._is_chat_simulation(obj):
            first_time, _ = self._get_first_last_chat_message_times(obj)
            return first_time

        # For voice agents, use started_at field
        return obj.started_at if hasattr(obj, "started_at") else None

    def get_transcript(self, obj):
        """Get transcripts excluding those with 'unknown' role.
        Skipped when detail_mode=False (list view) to reduce response size."""
        if not self.context.get("detail_mode", True):
            return []

        simulation_call_type = getattr(obj, "simulation_call_type", None)

        if (
            simulation_call_type is not None
            and simulation_call_type == CallExecution.SimulationCallType.TEXT
        ):
            if hasattr(obj, "chat_messages"):
                return ChatMessageSerializer(
                    obj.chat_messages.order_by("created_at"), many=True
                ).data
            return []

        # Filter out transcripts with 'unknown' speaker role
        if hasattr(obj, "transcripts"):
            filtered_transcripts = obj.transcripts.exclude(
                speaker_role=CallTranscript.SpeakerRole.UNKNOWN
            )
            call_metadata = getattr(obj, "call_metadata", None) or {}
            offset = call_metadata.get("recording_offset_ms", 0)
            return CallTranscriptSerializer(
                filtered_transcripts,
                many=True,
                context={"recording_offset_ms": offset},
            ).data
        return []

    def get_response_time(self, obj):
        """Convert response_time_ms to seconds"""
        # Handle both model instances and dictionaries (from grouping)
        if hasattr(obj, "response_time_ms"):
            response_time_ms = obj.response_time_ms
        else:
            response_time_ms = (
                obj.get("response_time_ms") if isinstance(obj, dict) else None
            )

        if response_time_ms is not None:
            return round(response_time_ms / 1000, 3)
        return None

    def get_eval_outputs(self, obj):
        """Get evaluation outputs in a structured format"""
        # Handle both model instances and dictionaries (from grouping)
        if hasattr(obj, "eval_outputs"):
            eval_outputs = obj.eval_outputs
        else:
            eval_outputs = obj.get("eval_outputs") if isinstance(obj, dict) else {}

        # For grouped results, return empty dict as eval outputs are not available
        if isinstance(obj, dict) and "count" in obj:
            return {}

        if not eval_outputs:
            return {}

        # Transform eval_outputs to a more structured format
        structured_outputs = {}
        for eval_id, eval_data in eval_outputs.items():
            if isinstance(eval_data, dict):
                if eval_data.get("status") == "pending":
                    structured_outputs[eval_id] = {}
                    continue
                raw_error = eval_data.get("error")
                is_error = bool(raw_error is True or raw_error == "error") or (
                    eval_data.get("status") == "error"
                )
                structured_outputs[eval_id] = {
                    "value": _normalize_eval_value(
                        eval_data.get("output"),
                        eval_data.get("output_type", ""),
                    ),
                    "reason": eval_data.get("reason", ""),
                    "type": eval_data.get("output_type", ""),
                    "name": eval_data.get("name", ""),
                    "error": is_error,
                    "status": eval_data.get(
                        "status", "error" if is_error else "completed"
                    ),
                    "skipped": bool(eval_data.get("skipped", False))
                    or eval_data.get("status") == "skipped",
                }

        return structured_outputs

    def get_eval_metrics(self, obj):
        """Get evaluation metrics in a format suitable for the UI"""
        # Handle both model instances and dictionaries (from grouping)
        if hasattr(obj, "eval_outputs"):
            eval_outputs = obj.eval_outputs
        else:
            eval_outputs = obj.get("eval_outputs") if isinstance(obj, dict) else {}

        # For grouped results, return empty dict as eval metrics are not available
        if isinstance(obj, dict) and "count" in obj:
            return {}

        if not eval_outputs:
            return {}

        # Get eval configs from context if available
        eval_configs = (
            self.context.get("eval_configs", {})
            if hasattr(self, "context") and self.context
            else {}
        )

        metrics = {}
        for eval_id, eval_data in eval_outputs.items():
            if isinstance(eval_data, dict):
                if eval_data.get("status") == "pending":
                    metrics[eval_id] = {}
                    continue
                raw_error = eval_data.get("error")
                is_error = bool(raw_error is True or raw_error == "error") or (
                    eval_data.get("status") == "error"
                )
                eval_config = eval_configs.get(eval_id)
                metrics[eval_id] = {
                    "id": eval_id,
                    "name": eval_data.get(
                        "name", eval_config.name if eval_config else ""
                    ),
                    "value": _normalize_eval_value(
                        eval_data.get("output"),
                        eval_data.get("output_type", ""),
                    ),
                    "reason": eval_data.get("reason", ""),
                    "type": eval_data.get("output_type", ""),
                    "visible": True,  # Default to visible
                    "error": is_error,
                    "status": eval_data.get(
                        "status", "error" if is_error else "completed"
                    ),
                    "skipped": bool(eval_data.get("skipped", False))
                    or eval_data.get("status") == "skipped",
                    "error_localizer": (
                        eval_config.error_localizer if eval_config else False
                    ),
                }

        return metrics

    def get_scenario_columns(self, obj):
        """Get scenario columns data based on scenario type"""
        # Handle both model instances and dictionaries (from grouping)
        if hasattr(obj, "call_metadata"):
            call_metadata = obj.call_metadata
        else:
            call_metadata = obj.get("call_metadata") if isinstance(obj, dict) else {}

        # For grouped results, return empty dict as scenario columns are not available
        if isinstance(obj, dict) and "count" in obj:
            return {}

        row_id = call_metadata.get("row_id")
        if row_id:
            row_id_str = str(row_id)

            # Use prefetched context if available (batch-loaded in view)
            ctx = self.context if hasattr(self, "context") and self.context else {}
            rows_map = ctx.get("rows_map")
            columns_by_dataset = ctx.get("columns_by_dataset")
            cells_by_row = ctx.get("cells_by_row")

            if rows_map is not None:
                if row_id_str not in rows_map:
                    return {}
                # Fast path: use prefetched data
                row = rows_map[row_id_str]
                ds_id = str(row.dataset.id) if row.dataset else None
                dataset_columns = columns_by_dataset.get(ds_id, []) if ds_id else []
                row_cells = cells_by_row.get(row_id_str, {})
            else:
                # Fallback: individual queries (for grouped results or missing context)
                try:
                    row = Row.all_objects.get(id=row_id)
                except Row.DoesNotExist:
                    return {}
                dataset_columns = Column.all_objects.filter(
                    id__in=row.dataset.column_order
                )
                row_cells = None

            scenario_data = {}
            for dataset_column in dataset_columns:
                try:
                    if row_cells is not None:
                        # Use prefetched cells
                        cell = row_cells.get(str(dataset_column.id))
                    else:
                        # Fallback: individual query
                        cell = Cell.all_objects.filter(
                            column=dataset_column,
                            row_id=row.id,
                        ).first()
                    cell_value = cell.value or "" if cell else ""
                except Exception:
                    cell_value = ""
                    traceback.print_exc()

                if dataset_column.name == "persona":
                    # Parse persona field if it's a string representation of a dict
                    persona_value = cell_value
                    if isinstance(persona_value, str):
                        try:
                            import ast

                            persona_data = ast.literal_eval(persona_value)
                        except (ValueError, SyntaxError):
                            persona_data = {}
                    elif isinstance(persona_value, dict):
                        persona_data = persona_value
                    else:
                        persona_data = {}
                    cell_value = persona_data

                scenario_data[str(dataset_column.id)] = {
                    "value": cell_value,
                    "visible": True,
                    "dataset_column_id": str(dataset_column.id),
                    "dataset_id": str(row.dataset.id),
                    "column_name": dataset_column.name,
                    "data_type": dataset_column.data_type,
                }
        else:
            scenario_data = {}

        return scenario_data

    def get_scenario_id(self, obj):
        """Get scenario ID"""
        return str(obj.scenario.id)

    # def get_error_localizer_tasks(self, obj):
    #     """Get error localizer tasks for this call execution"""
    #     try:
    #         from model_hub.models.error_localizer_model import (
    #             ErrorLocalizerSource,
    #             ErrorLocalizerTask,
    #         )

    #         # Handle both model instances and dictionaries (from grouping)
    #         if hasattr(obj, "id"):
    #             obj_id = obj.id
    #         else:
    #             obj_id = obj.get("id") if isinstance(obj, dict) else None

    #         # For grouped results, return empty list as error localizer tasks are not available
    #         if isinstance(obj, dict) and "count" in obj:
    #             return []

    #         if not obj_id:
    #             return []

    #         # Find error localizer tasks for this call execution
    #         # The source_id format is "call_execution_id_eval_config_id"
    #         call_execution_tasks = ErrorLocalizerTask.objects.filter(
    #             source=ErrorLocalizerSource.SIMULATE.value, source_id=str(obj_id)
    #         )

    #         error_localizer_data = []
    #         for task in call_execution_tasks:
    #             # Extract eval_config_id from source_id

    #             eval_config_id = task.metadata.get("eval_config_id")

    #             error_localizer_data.append(
    #                 {
    #                     "taskId": str(task.id),
    #                     "evalConfigId": eval_config_id,
    #                     "status": task.status,
    #                     "evalResult": task.eval_result,
    #                     "evalExplanation": task.eval_explanation,
    #                     "inputData": task.input_data,
    #                     "inputKeys": task.input_keys,
    #                     "inputTypes": task.input_types,
    #                     "rulePrompt": task.rule_prompt,
    #                     "errorAnalysis": task.error_analysis,
    #                     "selectedInputKey": task.selected_input_key,
    #                     "errorMessage": task.error_message,
    #                     "createdAt": (
    #                         task.created_at.isoformat() if task.created_at else None
    #                     ),
    #                     "updatedAt": (
    #                         task.updated_at.isoformat() if task.updated_at else None
    #                     ),
    #                 }
    #             )

    #         return error_localizer_data
    #     except Exception as e:
    #         # Log error but don't fail the serializer
    #         import logging

    #         logger = logging.getLogger(__name__)
    #         logger.error(
    #             f"Error fetching error localizer tasks for call execution {obj_id}: {str(e)}"
    #         )
    #         return []

    def get_rerun_snapshots(self, obj):
        """Get rerun snapshots for this call execution - only call_and_eval type"""
        try:
            # Handle both model instances and dictionaries (from grouping)
            if hasattr(obj, "id"):
                obj_id = obj.id
            else:
                obj_id = obj.get("id") if isinstance(obj, dict) else None

            # For grouped results, return empty list
            if isinstance(obj, dict) and "count" in obj:
                return []

            if not obj_id:
                return []

            # Use prefetched context if available (batch-loaded in view)
            ctx = self.context if hasattr(self, "context") and self.context else {}
            snapshots_by_call = ctx.get("snapshots_by_call")

            if snapshots_by_call is not None:
                snapshots = snapshots_by_call.get(str(obj_id), [])
            else:
                # Fallback: individual query
                snapshots = CallExecutionSnapshot.objects.filter(
                    call_execution_id=obj_id,
                    rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
                ).order_by("-snapshot_timestamp")

            # Serialize the snapshots
            snapshot_serializer = CallExecutionSnapshotSerializer(snapshots, many=True)
            return snapshot_serializer.data

        except Exception as e:
            # Log error but don't fail the serializer
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error fetching rerun snapshots for call execution {obj_id}: {str(e)}"
            )
            return []

    def get_is_snapshot(self, obj):
        """Check if this is a snapshot (when obj is a dict from flattened data)"""
        if isinstance(obj, dict):
            return obj.get("is_snapshot", False)
        return False

    def get_snapshot_timestamp(self, obj):
        """Get snapshot timestamp (when obj is a dict from flattened data)"""
        if isinstance(obj, dict):
            return obj.get("snapshot_timestamp")
        return None

    def get_rerun_type(self, obj):
        """Get rerun type (when obj is a dict from flattened data)"""
        if isinstance(obj, dict):
            return obj.get("rerun_type")
        return None

    def get_original_call_execution_id(self, obj):
        """Get original call execution ID (when obj is a dict from flattened data)"""
        if isinstance(obj, dict):
            return obj.get("original_call_execution_id")
        return None

    def get_overall_score(self, obj):
        """Get overall score, using CSAT score for chat agents if overall_score is null"""
        # Handle grouped/flattened data
        if isinstance(obj, dict):
            return obj.get("overall_score") or obj.get("avg_overall_score")

        # First check if overall_score is already set (should be CSAT for chat agents)
        if hasattr(obj, "overall_score") and obj.overall_score is not None:
            return obj.overall_score

        # For chat agents, fallback to CSAT score from conversation_metrics_data if overall_score is null
        if hasattr(obj, "test_execution") and obj.test_execution:
            agent_definition = obj.test_execution.run_test.agent_definition
            agent_type = agent_definition.agent_type if agent_definition else None
            if agent_type == "text":
                # For chat agents, check conversation_metrics_data for CSAT score
                if (
                    hasattr(obj, "conversation_metrics_data")
                    and obj.conversation_metrics_data
                ):
                    csat_score = obj.conversation_metrics_data.get("csat_score")
                    if csat_score is not None:
                        return float(csat_score)

        return None

    def get_total_tokens(self, obj):
        """Get total tokens from conversation_metrics_data"""
        if isinstance(obj, dict):
            # Handle grouped/flattened data
            return obj.get("total_tokens")
        if hasattr(obj, "conversation_metrics_data") and obj.conversation_metrics_data:
            return obj.conversation_metrics_data.get("total_tokens")
        return None

    def get_input_tokens(self, obj):
        """Get input tokens from conversation_metrics_data"""
        if isinstance(obj, dict):
            # Handle grouped/flattened data
            return obj.get("input_tokens")
        if hasattr(obj, "conversation_metrics_data") and obj.conversation_metrics_data:
            return obj.conversation_metrics_data.get("input_tokens")
        return None

    def get_output_tokens(self, obj):
        """Get output tokens from conversation_metrics_data"""
        if isinstance(obj, dict):
            # Handle grouped/flattened data
            return obj.get("output_tokens")
        if hasattr(obj, "conversation_metrics_data") and obj.conversation_metrics_data:
            return obj.conversation_metrics_data.get("output_tokens")
        return None

    def get_avg_latency_ms(self, obj):
        """Get average latency from conversation_metrics_data"""
        if isinstance(obj, dict):
            # Handle grouped/flattened data
            return obj.get("avg_latency_ms")
        if hasattr(obj, "conversation_metrics_data") and obj.conversation_metrics_data:
            return obj.conversation_metrics_data.get("avg_latency_ms")
        return None

    def get_turn_count(self, obj):
        """Get turn count from conversation_metrics_data"""
        if isinstance(obj, dict):
            # Handle grouped/flattened data
            return obj.get("turn_count")
        if hasattr(obj, "conversation_metrics_data") and obj.conversation_metrics_data:
            turn_count = obj.conversation_metrics_data.get("turn_count")
            if turn_count is not None:
                return turn_count

            # Voice parity with chat semantics: use agent (bot) turns.
            bot_message_count = obj.conversation_metrics_data.get("bot_message_count")
            if bot_message_count is not None:
                return bot_message_count

            # Voice conversations currently store message counts in detailed_data.
            # Legacy fallback when agent-only counts are unavailable.
            message_count = obj.conversation_metrics_data.get("message_count")
            if message_count is not None:
                return message_count

            user_message_count = obj.conversation_metrics_data.get("user_message_count")
            bot_message_count = obj.conversation_metrics_data.get("bot_message_count")
            if user_message_count is not None or bot_message_count is not None:
                return (user_message_count or 0) + (bot_message_count or 0)
        return None

    def get_agent_talk_percentage(self, obj):
        if isinstance(obj, dict):
            return obj.get("agent_talk_percentage")
        talk_ratio = getattr(obj, "talk_ratio", None)
        if talk_ratio is not None and talk_ratio >= 0:
            denominator = talk_ratio + 1
            if denominator > 0:
                return round((talk_ratio / denominator) * 100, 1)
        return None

    def get_csat_score(self, obj):
        """Get CSAT score from conversation_metrics_data"""
        if isinstance(obj, dict):
            # Handle grouped/flattened data
            return obj.get("csat_score")
        if hasattr(obj, "conversation_metrics_data") and obj.conversation_metrics_data:
            return obj.conversation_metrics_data.get("csat_score")
        return None

    def _get_processing_skip(self, obj):
        if isinstance(obj, dict):
            call_metadata = obj.get("call_metadata")
        else:
            call_metadata = getattr(obj, "call_metadata", None)
        if not isinstance(call_metadata, dict):
            return False, None

        skipped = bool(call_metadata.get("processing_skipped", False))
        reason = call_metadata.get("processing_skip_reason")
        return skipped, reason

    def get_processing_skipped(self, obj):
        skipped, _ = self._get_processing_skip(obj)
        return skipped

    def get_processing_skip_reason(self, obj):
        _, reason = self._get_processing_skip(obj)
        return reason


class CallExecutionSerializer(serializers.ModelSerializer):
    """Serializer for CallExecution model"""

    transcripts = serializers.SerializerMethodField()
    scenario_name = serializers.CharField(source="scenario.name", read_only=True)
    response_time_seconds = serializers.SerializerMethodField()
    system_metrics = serializers.SerializerMethodField()
    cost_breakdown = serializers.SerializerMethodField()
    error_localizer_tasks = serializers.SerializerMethodField()
    processing_skipped = serializers.SerializerMethodField()
    processing_skip_reason = serializers.SerializerMethodField()
    # Return customer_call_id as service_provider_call_id for frontend compatibility
    service_provider_call_id = serializers.CharField(
        source="customer_call_id", read_only=True
    )

    class Meta:
        model = CallExecution
        fields = [
            "id",
            "phone_number",
            "service_provider_call_id",
            "status",
            "started_at",
            "completed_at",
            "duration_seconds",
            "recording_url",
            "cost_cents",
            "call_metadata",
            "error_message",
            "scenario_name",
            "transcripts",
            "created_at",
            "updated_at",
            # Provider payload
            "provider_call_data",
            "stereo_recording_url",
            "ended_reason",
            "stt_cost_cents",
            "llm_cost_cents",
            "tts_cost_cents",
            "overall_score",
            "response_time_ms",
            "response_time_seconds",
            "assistant_id",
            "customer_number",
            "call_type",
            "ended_at",
            "analysis_data",
            "evaluation_data",
            "message_count",
            "transcript_available",
            "recording_available",
            "eval_outputs",
            "error_localizer_tasks",
            "call_summary",
            "agent_version",
            "customer_cost_cents",
            "system_metrics",
            "cost_breakdown",
            "customer_call_id",
            "simulation_call_type",
            "processing_skipped",
            "processing_skip_reason",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_transcripts(self, obj):
        """Get transcripts excluding those with 'unknown' role"""
        # Filter out transcripts with 'unknown' speaker role
        if hasattr(obj, "transcripts"):
            filtered_transcripts = obj.transcripts.exclude(
                speaker_role=CallTranscript.SpeakerRole.UNKNOWN
            )
            call_metadata = getattr(obj, "call_metadata", None) or {}
            offset = call_metadata.get("recording_offset_ms", 0)
            return CallTranscriptSerializer(
                filtered_transcripts,
                many=True,
                context={"recording_offset_ms": offset},
            ).data
        return []

    def get_response_time_seconds(self, obj):
        """Convert response_time_ms to seconds"""
        # Handle both model instances and dictionaries (from grouping)
        if hasattr(obj, "response_time_ms"):
            response_time_ms = obj.response_time_ms
        else:
            response_time_ms = (
                obj.get("response_time_ms") if isinstance(obj, dict) else None
            )

        if response_time_ms is not None:
            return round(response_time_ms / 1000, 3)
        return None

    def get_error_localizer_tasks(self, obj):
        """Get error localizer tasks for this call execution"""
        try:
            from model_hub.models.error_localizer_model import (
                ErrorLocalizerSource,
                ErrorLocalizerTask,
            )

            # Handle both model instances and dictionaries (from grouping)
            if hasattr(obj, "id"):
                obj_id = obj.id
            else:
                obj_id = obj.get("id") if isinstance(obj, dict) else None

            if not obj_id:
                return []

            # Find error localizer tasks for this call execution
            # The source_id format is "call_execution_id_eval_config_id"
            call_execution_tasks = ErrorLocalizerTask.objects.filter(
                source=ErrorLocalizerSource.SIMULATE.value, source_id=str(obj_id)
            )

            error_localizer_data = []
            for task in call_execution_tasks:
                # Extract eval_config_id from source_id
                eval_config_id = task.metadata.get("eval_config_id")

                error_localizer_data.append(
                    {
                        "task_id": str(task.id),
                        "eval_config_id": eval_config_id,
                        "status": task.status,
                        "eval_result": task.eval_result,
                        "eval_explanation": task.eval_explanation,
                        "input_data": task.input_data,
                        "input_keys": task.input_keys,
                        "input_types": task.input_types,
                        "rule_prompt": task.rule_prompt,
                        "error_analysis": task.error_analysis,
                        "selected_input_key": task.selected_input_key,
                        "error_message": task.error_message,
                        "created_at": (
                            task.created_at.isoformat() if task.created_at else None
                        ),
                        "updated_at": (
                            task.updated_at.isoformat() if task.updated_at else None
                        ),
                    }
                )

            return error_localizer_data
        except Exception as e:
            # Log error but don't fail the serializer
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error fetching error localizer tasks for call execution {obj.id}: {str(e)}"
            )
            return []

    def get_system_metrics(self, obj):
        metrics = getattr(obj, "customer_latency_metrics", None)
        if not isinstance(metrics, dict):
            return None

        # Support both payload shapes:
        # 1) {"systemMetrics": {...}, "turnLatencies": [...]}
        # 2) legacy flat {"endpointing": ..., "model": ...}
        system_metrics = metrics.get("systemMetrics")
        if isinstance(system_metrics, dict):
            return system_metrics

        if any(
            key in metrics
            for key in (
                "endpointing",
                "transcriber",
                "model",
                "voice",
                "turn",
                "avg_agent_latency_ms",
            )
        ):
            return metrics

        return None

    def get_simulation_call_type(self, obj):
        simulation_call_type = getattr(obj, "simulation_call_type", None)
        if simulation_call_type:
            return simulation_call_type
        return CallExecution.SimulationCallType.VOICE

    def get_cost_breakdown(self, obj):
        breakdown = getattr(obj, "customer_cost_breakdown", None)
        if isinstance(breakdown, dict):
            return breakdown
        return None

    def _get_processing_skip(self, obj):
        call_metadata = getattr(obj, "call_metadata", None)
        if not isinstance(call_metadata, dict):
            return False, None

        skipped = bool(call_metadata.get("processing_skipped", False))
        reason = call_metadata.get("processing_skip_reason")
        return skipped, reason

    def get_processing_skipped(self, obj):
        skipped, _ = self._get_processing_skip(obj)
        return skipped

    def get_processing_skip_reason(self, obj):
        _, reason = self._get_processing_skip(obj)
        return reason


class TestExecutionSerializer(serializers.ModelSerializer):
    """Serializer for TestExecution model"""

    calls = CallExecutionSerializer(many=True, read_only=True)
    run_test_name = serializers.CharField(source="run_test.name", read_only=True)
    agent_definition_name = serializers.CharField(
        source="run_test.agent_definition.agent_name", read_only=True
    )

    # New fields for simulator and agent definition used in this execution
    simulator_agent_name = serializers.CharField(
        source="simulator_agent.name", read_only=True
    )
    simulator_agent_id = serializers.UUIDField(
        source="simulator_agent.id", read_only=True
    )
    agent_definition_used_name = serializers.CharField(
        source="agent_definition.agent_name", read_only=True
    )
    agent_definition_used_id = serializers.UUIDField(
        source="agent_definition.id", read_only=True
    )

    # Call metrics fields per test execution
    calls_attempted = serializers.SerializerMethodField()
    calls_connected_percentage = serializers.SerializerMethodField()

    class Meta:
        model = TestExecution
        fields = [
            "id",
            "run_test",
            "run_test_name",
            "agent_definition_name",
            "status",
            "error_reason",
            "started_at",
            "completed_at",
            "total_scenarios",
            "total_calls",
            "completed_calls",
            "failed_calls",
            "execution_metadata",
            "duration_seconds",
            "success_rate",
            "calls",
            "created_at",
            "scenario_ids",
            "simulator_agent_name",
            "simulator_agent_id",
            "agent_definition_used_name",
            "agent_definition_used_id",
            "calls_attempted",
            "calls_connected_percentage",
        ]
        read_only_fields = ["id", "created_at"]

    def get_calls_attempted(self, obj):
        """Count calls that are not pending or queued for this test execution."""

        # Single query to get total and excluded calls
        call_counts = obj.calls.aggregate(
            total_calls=Count("id"),
            pending_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.PENDING)
            ),
            queued_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.REGISTERED)
            ),
        )

        # Calls attempted = total calls - pending calls - queued calls
        return (
            call_counts["total_calls"]
            - call_counts["pending_calls"]
            - call_counts["queued_calls"]
        )

    def get_calls_connected_percentage(self, obj):
        """Calculate percentage of calls connected (duration > 0 seconds) for this test execution."""

        # Single query to get all needed counts
        call_counts = obj.calls.aggregate(
            total_calls=Count("id"),
            pending_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.PENDING)
            ),
            queued_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.REGISTERED)
            ),
            connected_calls=Count("id", filter=Q(duration_seconds__gt=0)),
        )

        calls_attempted = (
            call_counts["total_calls"]
            - call_counts["pending_calls"]
            - call_counts["queued_calls"]
        )

        if calls_attempted == 0:
            return 0.0

        # Calculate percentage
        percentage = (call_counts["connected_calls"] / calls_attempted) * 100
        return round(percentage, 2)


class TestExecutionStatusSerializer(serializers.Serializer):
    """Serializer for test execution status response"""

    run_test_id = serializers.CharField()
    execution_id = serializers.CharField()
    status = serializers.CharField()
    total_scenarios = serializers.IntegerField()
    total_calls = serializers.IntegerField()
    completed_calls = serializers.IntegerField()
    failed_calls = serializers.IntegerField()
    success_rate = serializers.FloatField()
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField(allow_null=True)
    scenarios = serializers.ListField(child=serializers.DictField())
    error = serializers.CharField(allow_null=True)


class TestExecutionRequestSerializer(serializers.Serializer):
    """Serializer for test execution request"""

    run_test_id = serializers.CharField()
    scenario_ids = serializers.ListField(child=serializers.CharField(), required=False)
    execution_metadata = serializers.JSONField(required=False)


class AllActiveTestsSerializer(serializers.Serializer):
    """Serializer for all active tests response"""

    active_tests = serializers.DictField()
    total_active = serializers.IntegerField()


class ColumnOrderSerializer(serializers.Serializer):
    """Serializer for column order management"""

    column_name = serializers.CharField()
    id = serializers.CharField()
    visible = serializers.BooleanField()


class TestExecutionColumnOrderSerializer(serializers.Serializer):
    """Serializer for updating column order in test execution"""

    column_order = ColumnOrderSerializer(many=True)


class PerformanceSummarySerializer(serializers.Serializer):
    """Serializer for Performance Summary data"""

    test_run_performance_metrics = serializers.DictField(
        child=serializers.FloatField(),
        help_text="Performance metrics including pass rate, total test runs, and latest fail rate",
    )

    top_performing_scenarios = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField(),
            help_text="List of top performing scenarios with their performance scores",
        ),
        help_text="List of top performing scenarios",
    )


class TestExecutionAnalyticsSerializer(serializers.Serializer):
    """Serializer for Test Execution Analytics data"""

    fail_rate_over_test_runs = serializers.DictField(
        help_text="Fail rate data for scatter plot chart"
    )

    evaluation_categories_over_test_runs = serializers.DictField(
        help_text="Evaluation categories data for line graph chart"
    )

    metadata = serializers.DictField(help_text="Metadata about the analytics data")


class TestExecutionRerunSerializer(serializers.Serializer):
    """Serializer for bulk test execution rerun requests"""

    rerun_type = serializers.ChoiceField(
        choices=[
            ("eval_only", "Evaluation Only"),
            ("call_and_eval", "Call and Evaluation"),
        ],
        help_text="Type of rerun: evaluation only or call plus evaluation",
    )

    test_execution_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of specific test execution IDs to rerun",
    )

    select_all = serializers.BooleanField(
        default=False,
        help_text="Whether to rerun all test executions in the run test",
    )

    def validate(self, data):
        """Validate that either test_execution_ids or select_all is provided"""
        if not data.get("select_all") and not data.get("test_execution_ids"):
            raise serializers.ValidationError(
                "Either 'select_all' must be True or 'test_execution_ids' must be provided"
            )
        return data


class TestExecutionBulkDeleteSerializer(serializers.Serializer):
    """Serializer for bulk test execution delete requests"""

    test_execution_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of specific test execution IDs to delete",
    )

    select_all = serializers.BooleanField(
        default=False,
        help_text="Whether to delete all test executions in the run test",
    )

    def validate(self, data):
        """Validate that either test_execution_ids or select_all is provided"""
        if not data.get("select_all") and not data.get("test_execution_ids"):
            raise serializers.ValidationError(
                "Either 'select_all' must be True or 'test_execution_ids' must be provided"
            )
        return data


# Migrated to simulate/serializers/requests/run_test_evals.py
# Re-exported here for backward compatibility.
from simulate.serializers.requests.run_test_evals import (
    RunNewEvalsOnTestExecutionSerializer,
)

__all__ = ["RunNewEvalsOnTestExecutionSerializer"]
