"""Business logic for ALK sim ingestion.

All view code delegates here. Nothing in this module knows about DRF, requests,
or serializers — inputs are plain Python objects/dicts, outputs are dataclasses
or dicts. Callable from views, Temporal activities, tests, or the shell.

Recording/artifact URLs are supplied by the client as strings; the backend
never uploads bytes (same pattern as the Vapi provider adapter).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import structlog
from django.utils import timezone

from simulate.models import (
    CallExecution,
    RunTest,
    Scenarios,
    SimulatorAgent,
    TestExecution,
)
from simulate.models.test_execution import CallTranscript
from simulate.semantics import SupportedProviders
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
)
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from simulate.utils.websocket_notifications import notify_simulation_update
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_object_url, get_storage_client
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)

DEFAULT_BATCH_SIZE = 9

_STATUS_MAP = {
    "completed": CallExecution.CallStatus.COMPLETED,
    "failed": CallExecution.CallStatus.FAILED,
    "cancelled": CallExecution.CallStatus.CANCELLED,
}

_COST_FIELDS = (
    "stt_cost_cents",
    "llm_cost_cents",
    "tts_cost_cents",
    "storage_cost_cents",
    "cost_cents",
)

_TRANSCRIPT_ROLE_TO_METRIC_ROLE = {
    CallTranscript.SpeakerRole.USER: "user",
    CallTranscript.SpeakerRole.ASSISTANT: "bot",
}


@dataclass(frozen=True)
class BatchCreateResult:
    call_execution_ids: list[str]
    has_more: bool
    batched_scenarios: list[str]


@dataclass(frozen=True)
class IngestionResult:
    call_execution_id: str
    status: str
    eval_dispatched: bool


class ALKSimulateIngestionError(Exception):
    """Raised when a LiveKit ingestion request cannot be satisfied.

    Views translate this into a 400 response; internal callers can catch and
    branch. The message is safe to surface to the caller — do not include
    sensitive detail.
    """


class ALKSimulateInvalidCallTypeError(ALKSimulateIngestionError):
    """The target CallExecution is not a VOICE row."""


class ALKSimulateNothingToCreateError(ALKSimulateIngestionError):
    """All scenarios and dataset rows for this test execution are already batched."""


_ALK_RECORDING_PREFIX = "alk-sim/recordings"
_CONTENT_TYPE_BY_EXT = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
}


@dataclass(frozen=True)
class RecordingUploadResult:
    recording_url: str
    object_key: str


def store_alk_recording(
    call_execution: CallExecution,
    audio_bytes: bytes,
    *,
    filename: str | None = None,
) -> RecordingUploadResult:
    """Persist an ALK-supplied recording to the shared upload bucket.

    Uses the storage client directly (``put_object``) — bypasses
    ``tfc.utils.storage.upload_audio_to_s3`` because that helper calls
    ``ensure_bucket``/``bucket_exists``, which needs list-bucket permission
    the prod HMAC credentials do not grant. Bucket lifecycle here is owned
    by infra (Terraform / Helm); this path only writes objects into it.
    """
    if call_execution.simulation_call_type != CallExecution.SimulationCallType.VOICE:
        raise ALKSimulateInvalidCallTypeError(
            "Recording uploads are only valid for VOICE call executions"
        )
    if not audio_bytes:
        raise ALKSimulateIngestionError("recording upload was empty")

    ext = _extension_from_filename(filename)
    content_type = _CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")
    object_key = f"{_ALK_RECORDING_PREFIX}/{call_execution.id}/{uuid.uuid4().hex}.{ext}"
    client = get_storage_client()
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=object_key,
        data=BytesIO(audio_bytes),
        length=len(audio_bytes),
        content_type=content_type,
    )
    recording_url = get_object_url(UPLOAD_BUCKET_NAME, object_key)
    return RecordingUploadResult(
        recording_url=recording_url,
        object_key=object_key,
    )


def _extension_from_filename(filename: str | None) -> str:
    if not filename:
        return "wav"
    _, _, tail = filename.rpartition(".")
    tail = tail.lower().strip()
    return tail if tail and 1 <= len(tail) <= 5 else "wav"


def create_alk_sim_test_execution(
    run_test: RunTest,
    *,
    scenario_ids: list[str] | None = None,
    simulator_agent: SimulatorAgent | None = None,
) -> TestExecution:
    """Create a TestExecution shell for an ALK-owned run.

    Unlike ``RunTestExecutionView`` this does not dispatch Temporal or Celery
    orchestration — the SDK already ran the simulation and will PATCH results
    into the CallExecution rows created by ``create_alk_sim_call_execution_batch``.
    """
    active_scenario_ids = list(
        run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
    )
    if scenario_ids:
        requested = {str(sid) for sid in scenario_ids}
        allowed = {str(sid) for sid in active_scenario_ids}
        chosen = [sid for sid in scenario_ids if str(sid) in allowed]
        missing = requested - allowed
        if missing:
            raise ALKSimulateIngestionError(
                f"Scenarios not attached to this run test: {sorted(missing)}"
            )
    else:
        chosen = [str(sid) for sid in active_scenario_ids]

    if not chosen:
        raise ALKSimulateIngestionError(
            "run_test has no scenarios; attach at least one before starting an ALK execution"
        )

    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        started_at=timezone.now(),
        total_scenarios=len(chosen),
        scenario_ids=[str(sid) for sid in chosen],
        picked_up_by_executor=True,
        simulator_agent=simulator_agent or run_test.simulator_agent,
        agent_definition=run_test.agent_definition,
        agent_version=run_test.agent_version,
    )


def create_alk_sim_call_execution_batch(
    test_execution: TestExecution,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> BatchCreateResult:
    """Create up to `batch_size + 1` PENDING VOICE CallExecution rows.

    The `has_more` flag is set when the loop breaks because the current batch
    is full — the caller iterates until `has_more == False`.
    """
    run_test = test_execution.run_test
    agent_definition = run_test.agent_definition
    selected_version = test_execution.agent_version or agent_definition.latest_version

    processed_row_ids, processed_scenarios = _already_batched_ids(test_execution)

    test_executor = TestExecutor(initialize_voice_service=False)
    simulator_agent_cache: dict[str, SimulatorAgent] = {}

    batched: list[CallExecution] = []
    batched_scenarios: set[str] = set()
    has_more = False

    for scenario_id in test_execution.scenario_ids:
        if len(batched) > batch_size:
            has_more = True
            break

        try:
            scenario = Scenarios.objects.select_related(
                "simulator_agent", "dataset", "agent_definition"
            ).get(id=scenario_id, deleted=False)
        except Scenarios.DoesNotExist:
            logger.warning(
                "livekit_batch_scenario_missing", scenario_id=str(scenario_id)
            )
            continue

        simulator_agent = simulator_agent_cache.get(
            scenario_id
        ) or _resolve_simulator_agent(scenario, run_test, selected_version)
        simulator_agent_cache[scenario_id] = simulator_agent
        base_prompt = simulator_agent.prompt

        if scenario.dataset:
            remaining = _remaining_dataset_rows(
                scenario, test_executor, processed_row_ids.get(scenario_id, set())
            )
            for row_id in remaining:
                if len(batched) > batch_size:
                    has_more = True
                    break
                row_data_info = test_executor._get_row_data_and_generate_prompt(
                    row_id=row_id,
                    base_prompt=base_prompt,
                    agent_version=selected_version,
                )
                batched.append(
                    _build_call_execution(
                        test_execution=test_execution,
                        scenario=scenario,
                        agent_definition=agent_definition,
                        selected_version=selected_version,
                        simulator_agent=simulator_agent,
                        base_prompt=base_prompt,
                        row_id=row_id,
                        row_data_info=row_data_info,
                    )
                )
                batched_scenarios.add(scenario_id)
        else:
            if scenario_id in processed_scenarios:
                continue
            if len(batched) >= batch_size:
                break
            batched.append(
                _build_call_execution(
                    test_execution=test_execution,
                    scenario=scenario,
                    agent_definition=agent_definition,
                    selected_version=selected_version,
                    simulator_agent=simulator_agent,
                    base_prompt=base_prompt,
                    row_id=None,
                    row_data_info=None,
                )
            )
            batched_scenarios.add(scenario_id)

    if not batched:
        raise ALKSimulateNothingToCreateError(
            "No remaining call executions to create. All scenarios and rows "
            "have been processed."
        )

    created = CallExecution.objects.bulk_create(batched)
    return BatchCreateResult(
        call_execution_ids=[str(c.id) for c in created],
        has_more=has_more,
        batched_scenarios=sorted(batched_scenarios),
    )


def ingest_alk_sim_result(
    call_execution: CallExecution,
    organization,
    payload: dict[str, Any],
) -> IngestionResult:
    """Apply a finished LiveKit result to a CallExecution.

    Idempotent for evaluation dispatch: a second call updates fields but does
    not dispatch a second evaluation (guarded by `call_metadata['eval_started']`).
    """
    if call_execution.simulation_call_type != CallExecution.SimulationCallType.VOICE:
        raise ALKSimulateInvalidCallTypeError(
            "LiveKit result can only be submitted to VOICE call executions"
        )

    _apply_payload(call_execution, payload)

    eval_dispatched = False
    if call_execution.status == CallExecution.CallStatus.COMPLETED:
        _dispatch_csat_once(call_execution)
        eval_dispatched = _dispatch_evaluations_once(call_execution)

    try:
        notify_simulation_update(
            organization_id=str(organization.id),
            run_test_id=str(call_execution.test_execution.run_test_id),
            test_execution_id=str(call_execution.test_execution_id),
        )
    except Exception:
        logger.exception(
            "alk_sim_notify_failed", call_execution_id=str(call_execution.id)
        )

    # Roll up parent TestExecution when children reach terminal states — mirrors
    # store_chat_messages so the frontend's simulation-runs grid actually moves
    # off "pending" once a call lands.
    try:
        from simulate.tasks.chat_sim import monitor_test_execution_for_chat

        monitor_test_execution_for_chat.apply_async(
            args=(str(call_execution.test_execution_id),)
        )
    except Exception:
        logger.exception(
            "alk_sim_monitor_dispatch_failed",
            call_execution_id=str(call_execution.id),
        )

    return IngestionResult(
        call_execution_id=str(call_execution.id),
        status="ingested",
        eval_dispatched=eval_dispatched,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _already_batched_ids(
    test_execution: TestExecution,
) -> tuple[dict[str, set[str]], set[str]]:
    processed_row_ids_by_scenario: dict[str, set[str]] = {}
    processed_scenario_ids: set[str] = set()
    voice_calls = test_execution.calls.filter(
        simulation_call_type=CallExecution.SimulationCallType.VOICE
    )
    for call in voice_calls:
        scenario_id = str(call.scenario_id)
        if call.row_id:
            processed_row_ids_by_scenario.setdefault(scenario_id, set()).add(
                str(call.row_id)
            )
        else:
            processed_scenario_ids.add(scenario_id)
    return processed_row_ids_by_scenario, processed_scenario_ids


def _remaining_dataset_rows(
    scenario: Scenarios,
    test_executor: TestExecutor,
    processed_rows: set[str],
) -> Iterable[str]:
    all_row_ids = test_executor._parse_dataset_scenario(scenario)
    return [rid for rid in all_row_ids if str(rid) not in processed_rows]


def _resolve_simulator_agent(scenario, run_test, selected_version) -> SimulatorAgent:
    simulator_agent = scenario.simulator_agent or run_test.simulator_agent
    if simulator_agent is not None:
        return simulator_agent
    fallback_prompt = generate_simulator_agent_prompt(agent_version=selected_version)
    simulator_agent = SimulatorAgent.objects.create(
        name=scenario.name,
        prompt=fallback_prompt,
        voice_provider="livekit",
        voice_name="alk-simulator",
        model="gpt-4",
        llm_temperature=0.7,
        initial_message="Hi!",
        max_call_duration_in_minutes=30,
        interrupt_sensitivity=0.5,
        conversation_speed=1.0,
        finished_speaking_sensitivity=0.5,
        initial_message_delay=0,
        organization=scenario.organization,
        workspace=scenario.workspace,
    )
    scenario.simulator_agent = simulator_agent
    scenario.save(update_fields=["simulator_agent"])
    return simulator_agent


def _build_call_execution(
    *,
    test_execution: TestExecution,
    scenario: Scenarios,
    agent_definition,
    selected_version,
    simulator_agent: SimulatorAgent,
    base_prompt: str,
    row_id: str | None,
    row_data_info: dict | None,
) -> CallExecution:
    row_data_info = row_data_info or {}
    system_prompt = row_data_info.get("dynamic_prompt", base_prompt)
    return CallExecution(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="",
        status=CallExecution.CallStatus.PENDING,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        agent_version=selected_version,
        row_id=row_id,
        call_metadata={
            "call_channel": "livekit",
            "external_runner": "alk",
            "row_id": row_id,
            "row_data": row_data_info.get("row_data", {}),
            "dataset_id": row_data_info.get("dataset_id"),
            "base_prompt": base_prompt,
            "agent_description": agent_definition.description,
            "dynamic_prompt": row_data_info.get("dynamic_prompt"),
            "language": "en",
            "initial_message": simulator_agent.initial_message,
            "voice_name": simulator_agent.voice_name,
            "conversation_speed": simulator_agent.conversation_speed,
            "interrupt_sensitivity": simulator_agent.interrupt_sensitivity,
            "finished_speaking_sensitivity": simulator_agent.finished_speaking_sensitivity,
            "max_call_duration_in_minutes": simulator_agent.max_call_duration_in_minutes,
            "initial_message_delay": simulator_agent.initial_message_delay,
            "system_prompt": system_prompt,
        },
    )


def _apply_payload(call_execution: CallExecution, payload: dict[str, Any]) -> None:
    call_execution.status = _STATUS_MAP[payload["status"]]

    started_at = payload.get("started_at")
    ended_at = payload.get("ended_at")
    if started_at and not call_execution.started_at:
        call_execution.started_at = started_at
    if ended_at:
        call_execution.ended_at = ended_at
        call_execution.completed_at = ended_at

    duration = payload.get("duration_seconds")
    if duration is not None:
        call_execution.duration_seconds = duration
    elif call_execution.started_at and call_execution.ended_at:
        call_execution.duration_seconds = int(
            (call_execution.ended_at - call_execution.started_at).total_seconds()
        )

    for field in ("ended_reason", "error_message", "call_summary"):
        value = payload.get(field)
        if value:
            setattr(call_execution, field, value)

    recording_url = payload.get("recording_url")
    if recording_url:
        call_execution.recording_url = recording_url
        call_execution.recording_available = True

    stereo = payload.get("stereo_recording_url")
    if stereo:
        call_execution.stereo_recording_url = stereo

    costs = payload.get("costs") or {}
    for field in _COST_FIELDS:
        if costs.get(field) is not None:
            setattr(call_execution, field, costs[field])

    provider_data = payload.get("provider_call_data")
    if provider_data is not None:
        existing = call_execution.provider_call_data or {}
        if provider_data and set(provider_data.keys()).issubset(SupportedProviders):
            existing.update(provider_data)
        else:
            existing["livekit"] = provider_data
        call_execution.provider_call_data = existing

    if payload.get("call_metadata"):
        merged = call_execution.call_metadata or {}
        merged.update(payload["call_metadata"])
        call_execution.call_metadata = merged

    segments = payload.get("transcript") or []
    if (
        segments
        and not CallTranscript.objects.filter(call_execution=call_execution).exists()
    ):
        CallTranscript.objects.bulk_create(
            [
                CallTranscript(
                    call_execution=call_execution,
                    speaker_role=seg["speaker_role"],
                    content=seg["content"],
                    start_time_ms=seg.get("start_time_ms") or 0,
                    end_time_ms=seg.get("end_time_ms") or 0,
                    confidence_score=(
                        seg["confidence_score"]
                        if seg.get("confidence_score") is not None
                        else 1.0
                    ),
                )
                for seg in segments
            ]
        )
        call_execution.transcript_available = True

    _apply_conversation_metrics(call_execution)
    call_execution.save()


def _apply_conversation_metrics(call_execution: CallExecution) -> None:
    """Compute + persist conversation metrics from CallTranscript.

    Mirrors ee/voice/temporal/activities/voice_large.py:
    - runs ConversationMetricsCalculator on a NormalizedTranscriptData
      built from CallTranscript rows
    - writes individual CallExecution columns + conversation_metrics_data
    """
    from ee.voice.services.conversation_metrics import (
        ConversationMetricsCalculator,
    )
    from ee.voice.services.types.voice import (
        NormalizedTranscriptData,
        TranscriptMessage,
    )

    transcripts = list(
        CallTranscript.objects.filter(call_execution=call_execution).order_by(
            "start_time_ms"
        )
    )
    if not transcripts:
        return

    messages: list[TranscriptMessage] = []
    for t in transcripts:
        role = _TRANSCRIPT_ROLE_TO_METRIC_ROLE.get(t.speaker_role)
        if role is None:
            continue
        start_s = (t.start_time_ms or 0) / 1000.0
        end_s = (t.end_time_ms or 0) / 1000.0 if t.end_time_ms else None
        messages.append(
            TranscriptMessage(
                role=role,
                content=t.content or "",
                time=start_s,
                end_time=end_s,
                duration=(end_s - start_s) if end_s is not None else None,
            )
        )
    if not messages:
        return

    is_outbound = (call_execution.call_metadata or {}).get(
        "call_direction"
    ) == "outbound"
    normalized = NormalizedTranscriptData(messages=messages)
    calculator = ConversationMetricsCalculator(
        voice_service_provider=ProviderChoices.LIVEKIT
    )
    metrics = calculator.calculate_metrics_from_normalized(
        normalized, is_outbound=is_outbound
    )

    call_execution.avg_agent_latency_ms = metrics.avg_agent_latency_ms
    call_execution.user_interruption_count = metrics.user_interruption_count
    call_execution.user_interruption_rate = metrics.user_interruption_rate
    call_execution.ai_interruption_count = metrics.ai_interruption_count
    call_execution.ai_interruption_rate = metrics.ai_interruption_rate
    call_execution.user_wpm = metrics.user_wpm
    call_execution.bot_wpm = metrics.bot_wpm
    call_execution.talk_ratio = metrics.talk_ratio
    call_execution.avg_stop_time_after_interruption_ms = (
        metrics.avg_stop_time_after_interruption_ms
    )

    detailed_data = dict(metrics.detailed_data or {})

    # Preserve csat_score across recomputes — CSAT is written by a later task
    # into conversation_metrics_data; a second (idempotent) ingest must not
    # wipe it when it rebuilds the metrics blob.
    existing_csat = (call_execution.conversation_metrics_data or {}).get("csat_score")
    if existing_csat is not None:
        detailed_data["csat_score"] = existing_csat

    # Fold in the target agent's LLM token usage (provider-reported, stored on
    # provider_call_data by ingestion) the same way voice_large.py does, so the
    # frontend's token cells and the KPI aggregate light up.
    token_usage = _extract_llm_token_usage(call_execution.provider_call_data)
    if token_usage is not None:
        if token_usage.get("input_tokens") is not None:
            detailed_data["input_tokens"] = token_usage["input_tokens"]
        if token_usage.get("output_tokens") is not None:
            detailed_data["output_tokens"] = token_usage["output_tokens"]
        if token_usage.get("total_tokens") is not None:
            detailed_data["total_tokens"] = token_usage["total_tokens"]

    if call_execution.message_count is None:
        call_execution.message_count = len(messages)
    call_execution.conversation_metrics_data = detailed_data

    # Backfill duration from transcript span when the SDK payload carried no
    # explicit duration and no start/end timestamps — the last segment's
    # end offset is the best observed call length.
    if call_execution.duration_seconds is None:
        last_end_ms = max(
            (t.end_time_ms or 0 for t in transcripts),
            default=0,
        )
        if last_end_ms > 0:
            call_execution.duration_seconds = int(round(last_end_ms / 1000.0))


def _extract_llm_token_usage(
    provider_call_data: dict | None,
) -> dict[str, int] | None:
    """Return normalized {input_tokens, output_tokens, total_tokens} usage.

    Mirrors ee/voice get_normalized_transcript_data: reads the normalized
    ``usage.llm`` bucket the SDK writes under each provider key. Providers that
    only report a total (e.g. Retell) yield total_tokens without a split.
    Returns None when no LLM usage was reported.
    """
    if not isinstance(provider_call_data, dict):
        return None
    for provider_data in provider_call_data.values():
        if not isinstance(provider_data, dict):
            continue
        usage = provider_data.get("usage")
        if not isinstance(usage, dict):
            continue
        llm_usage = usage.get("llm")
        if not isinstance(llm_usage, dict):
            continue

        prompt = _coerce_token(
            llm_usage.get("prompt_tokens", llm_usage.get("promptTokens"))
        )
        completion = _coerce_token(
            llm_usage.get("completion_tokens", llm_usage.get("completionTokens"))
        )
        total = _coerce_token(
            llm_usage.get("total_tokens", llm_usage.get("totalTokens"))
        )

        result: dict[str, int] = {}
        if prompt is not None:
            result["input_tokens"] = prompt
        if completion is not None:
            result["output_tokens"] = completion
        if total is not None:
            result["total_tokens"] = total
        elif prompt is not None or completion is not None:
            result["total_tokens"] = (prompt or 0) + (completion or 0)

        if any(v for v in result.values()):
            return result
    return None


def _coerce_token(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dispatch_csat_once(call_execution: CallExecution) -> None:
    call_metadata = call_execution.call_metadata or {}
    if call_metadata.get("csat_dispatched"):
        return
    call_metadata["csat_dispatched"] = True
    call_execution.call_metadata = call_metadata
    call_execution.save(update_fields=["call_metadata"])
    try:
        from simulate.tasks.alk_sim import calculate_alk_voice_csat_score

        calculate_alk_voice_csat_score.apply_async(args=(str(call_execution.id),))
    except Exception as dispatch_error:
        logger.exception(
            "alk_csat_dispatch_failed",
            call_execution_id=str(call_execution.id),
        )
        call_metadata["csat_dispatched"] = False
        call_metadata["csat_dispatch_failed"] = str(dispatch_error)
        call_execution.call_metadata = call_metadata
        call_execution.save(update_fields=["call_metadata"])


def _dispatch_evaluations_once(call_execution: CallExecution) -> bool:
    call_metadata = call_execution.call_metadata or {}
    if call_metadata.get("eval_started"):
        return False
    call_metadata["eval_started"] = True
    call_execution.call_metadata = call_metadata
    call_execution.save(update_fields=["call_metadata"])
    try:
        _run_simulate_evaluations_task.apply_async(args=(str(call_execution.id),))
        return True
    except Exception as dispatch_error:
        logger.exception(
            "livekit_eval_dispatch_failed",
            call_execution_id=str(call_execution.id),
        )
        call_metadata["eval_started"] = False
        call_metadata["eval_dispatch_failed"] = str(dispatch_error)
        call_execution.call_metadata = call_metadata
        call_execution.save(update_fields=["call_metadata"])
        return False
