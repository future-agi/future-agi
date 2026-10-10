"""Identifier-only Temporal boundary for call audio analysis (R11/R12).

Imports stay light so workflow dataclasses do not load Django or DSP libraries.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from temporalio import activity


@dataclass
class ScheduleAudioAnalysisInput:
    call_id: str
    org_id: str | None = None
    workspace_id: str | None = None


@dataclass
class ScheduleAudioAnalysisOutput:
    scheduled: bool
    analysis_id: str | None = None
    generation: int = 0
    deadline_at: str | None = None
    skip_reason: str | None = None


@dataclass
class AudioAnalysisInput:
    call_id: str
    analysis_id: str
    generation: int
    deadline_at: str
    org_id: str | None = None
    workspace_id: str | None = None


@dataclass
class AnalyzeCallAudioInput:
    call_id: str
    analysis_id: str
    generation: int
    attempt_deadline_at: str
    org_id: str | None = None
    workspace_id: str | None = None


@dataclass
class AnalyzeCallAudioOutput:
    committed: bool
    state: str
    reasons: dict[str, str | None]


@dataclass
class FinalizeFailureInput:
    call_id: str
    analysis_id: str
    generation: int
    reason: str


def _identity(call):
    from django.conf import settings

    from ee.voice.services.audio_analysis.constants import (
        PITCH_ALGORITHM_VERSION,
        PREPROCESSING_VERSION,
        SNR_ALGORITHM_VERSION,
    )
    from ee.voice.services.audio_analysis.identity import compute_analysis_id
    from ee.voice.services.audio_provenance import validate_manifest

    prov = call.audio_provenance
    artifact = validate_manifest(prov, str(call.id), settings.UPLOAD_BUCKET_NAME)
    run = call.test_execution.run_test
    return compute_analysis_id(
        org_id=run.organization_id,
        workspace_id=run.workspace_id,
        call_id=call.id,
        generation=call.audio_analysis_generation,
        target_object_key=artifact.object_key,
        target_object_version_or_etag=artifact.object_version_or_etag,
        manifest_version=prov["manifest_version"],
        mapping_version=prov["target"]["mapping_version"],
        preprocessing_version=PREPROCESSING_VERSION,
        pitch_algorithm_version=PITCH_ALGORITHM_VERSION,
        snr_algorithm_version=SNR_ALGORITHM_VERSION,
        vqi_model_sha256=settings.VOICE_AUDIO_METRICS_DNSMOS_MODEL_SHA256,
        calibration_id=settings.VOICE_AUDIO_METRICS_CALIBRATION_SHA256,
    )


def _schedule_audio_analysis(
    input: ScheduleAudioAnalysisInput,
) -> ScheduleAudioAnalysisOutput:
    from django.conf import settings
    from django.db import transaction

    from ee.voice.services.audio_analysis.constants import (
        PIPELINE_VERSION,
        audio_metrics_enabled_for_org,
    )
    from ee.voice.services.audio_analysis.envelope import empty_envelope
    from ee.voice.services.audio_analysis.errors import AudioProvenanceError
    from simulate.models.test_execution import CallExecution

    if not settings.VOICE_AUDIO_METRICS_ENABLED:
        return ScheduleAudioAnalysisOutput(False, skip_reason="flag_off")
    with transaction.atomic():
        call = (
            CallExecution.objects.select_for_update(of=("self",))
            .select_related("test_execution__run_test")
            .filter(id=input.call_id, deleted=False)
            .first()
        )
        if call is None:
            return ScheduleAudioAnalysisOutput(False, skip_reason="missing_call")
        generation = call.audio_analysis_generation

        def skip(reason, state="not_requested"):
            call.audio_metrics = empty_envelope(generation, reason)
            call.audio_metrics["state"] = state
            if state == "failed":
                for entry in call.audio_metrics["metrics"].values():
                    entry["state"] = "failed"
            call.save(update_fields=["audio_metrics"])
            return ScheduleAudioAnalysisOutput(
                False, generation=generation, skip_reason=reason
            )

        if call.simulation_call_type == "text":
            return skip("not_applicable")
        run = call.test_execution.run_test
        if not audio_metrics_enabled_for_org(run.organization_id):
            return skip("not_enabled")
        if _tenant_mismatch(input, run):
            return skip("analysis_error", "failed")
        prov = call.audio_provenance or {}
        if not prov.get("target"):
            reason = (
                (prov.get("unsupported_reason") or "unknown_agent_track")
                if prov.get("artifacts")
                else "no_recording"
            )
            return skip(reason, "unavailable")
        try:
            analysis_id = _identity(call)
        except AudioProvenanceError as exc:
            return skip(exc.reason, exc.state)
        stored = call.audio_metrics or {}
        if stored.get("analysis_id") == analysis_id and stored.get("state") != "failed":
            return ScheduleAudioAnalysisOutput(
                False,
                analysis_id,
                generation,
                stored.get("deadline_at"),
                "duplicate_identity",
            )
        now = datetime.now(UTC)
        env = empty_envelope(generation)
        env.update(
            analysis_id=analysis_id,
            state="pending",
            scheduled_at=now.isoformat(),
            deadline_at=(now + timedelta(minutes=30)).isoformat(),
            pipeline_version=PIPELINE_VERSION,
        )
        for entry in env["metrics"].values():
            entry.update(state="pending", reason=None)
        if not settings.VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH:
            env["metrics"]["voice_quality_index"].update(
                state="unavailable", reason="model_unavailable"
            )
        elif not settings.VOICE_AUDIO_METRICS_CALIBRATION_PATH:
            env["metrics"]["voice_quality_index"].update(
                state="unavailable", reason="not_validated"
            )
        call.audio_metrics = env
        call.save(update_fields=["audio_metrics"])
        return ScheduleAudioAnalysisOutput(
            True, analysis_id, generation, env["deadline_at"]
        )


def _tenant_mismatch(input, run):
    return (input.org_id is not None and str(run.organization_id) != input.org_id) or (
        input.workspace_id is not None
        and str(run.workspace_id or "") != input.workspace_id
    )


def _load_current(input):
    from ee.voice.services.audio_analysis.errors import StaleAnalysisError
    from simulate.models.test_execution import CallExecution

    call = (
        CallExecution.objects.select_related("test_execution__run_test")
        .filter(id=input.call_id, deleted=False)
        .first()
    )
    if call is None:
        raise StaleAnalysisError("tombstone")
    if call.audio_analysis_generation != input.generation:
        raise StaleAnalysisError("stale_generation")
    if (call.audio_metrics or {}).get("analysis_id") != input.analysis_id:
        raise StaleAnalysisError("stale_identity")
    return call


def _terminal_pending(env, reason, state="failed"):
    from ee.voice.services.audio_analysis.envelope import derive_state

    for entry in env["metrics"].values():
        if entry["state"] == "pending":
            entry.update(value=None, state=state, reason=reason)
    env.update(
        state=derive_state(env["metrics"]), computed_at=datetime.now(UTC).isoformat()
    )
    return env


def _output(env, committed):
    return AnalyzeCallAudioOutput(
        committed,
        env["state"],
        {name: entry["reason"] for name, entry in env["metrics"].items()},
    )


def _finalize_audio_analysis_failure(input: FinalizeFailureInput) -> None:
    from ee.voice.services.audio_analysis.commit import commit_envelope
    from ee.voice.services.audio_analysis.errors import StaleAnalysisError

    if input.reason not in {"timeout", "analysis_error"}:
        raise ValueError("Invalid audio finalization reason")
    try:
        call = _load_current(input)
        if call.audio_metrics.get("state") != "pending":
            return
        env = _terminal_pending(call.audio_metrics, input.reason)
        commit_envelope(input.call_id, input.generation, input.analysis_id, env)
    except StaleAnalysisError:
        return


def _commit_result(input, envelope, *, provenance=None, decoded=None):
    from django.db import transaction

    from ee.voice.services.audio_analysis.commit import commit_envelope
    from ee.voice.services.audio_analysis.errors import StaleAnalysisError
    from simulate.models.test_execution import CallExecution

    with transaction.atomic():
        # Lock source and envelope together; replacement after scheduling must
        # not let an old waveform commit against the same pending envelope.
        CallExecution.objects.select_for_update().filter(pk=input.call_id).first()
        current = _load_current(input)
        if provenance is not None and current.audio_provenance != provenance:
            raise StaleAnalysisError("stale_identity")
        if (
            current.audio_metrics.get("computed_at")
            and current.audio_metrics["state"] != "pending"
        ):
            return _output(current.audio_metrics, False)
        if decoded is not None:
            if _identity(current) != input.analysis_id:
                raise StaleAnalysisError("stale_identity")
            artifact = current.audio_provenance["artifacts"][
                current.audio_provenance["target"]["artifact_index"]
            ]
            artifact.update(
                sha256=decoded.sha256,
                codec=decoded.codec,
                sample_rate_hz=decoded.sample_rate_hz,
                channels=decoded.original_channel_count,
                sample_count=len(decoded.waveform),
            )
            current.save(update_fields=["audio_provenance"])
        commit_envelope(input.call_id, input.generation, input.analysis_id, envelope)
    return _output(envelope, True)


def _analyze(input, checkpoint):
    import copy

    from django.conf import settings
    from django.db import close_old_connections

    from ee.voice.services.audio_analysis.constants import PREPROCESSING_VERSION
    from ee.voice.services.audio_analysis.decode import decode_audio, fetch_audio
    from ee.voice.services.audio_analysis.envelope import derive_state
    from ee.voice.services.audio_analysis.errors import (
        AudioDeterministicError,
        StaleAnalysisError,
    )
    from ee.voice.services.audio_analysis.pitch_snr import analyze_pitch_snr
    from ee.voice.services.audio_analysis.vqi import analyze_vqi
    from ee.voice.services.audio_provenance import validate_manifest
    from ee.voice.services.audio_storage import get_audio_storage

    close_old_connections()
    try:
        call = _load_current(input)
        env = call.audio_metrics
        original_env = copy.deepcopy(env)
        if env.get("computed_at") and env["state"] != "pending":
            return _output(env, False)
        provenance = call.audio_provenance
        decoded = None
        try:
            if _tenant_mismatch(input, call.test_execution.run_test):
                activity.logger.warning(
                    "audio_metrics.security tenant_mismatch",
                    extra={"analysis_id": input.analysis_id},
                )
                raise AudioDeterministicError("analysis_error")
            artifact = validate_manifest(
                provenance, input.call_id, settings.UPLOAD_BUCKET_NAME
            )
            if _identity(call) != input.analysis_id:
                raise StaleAnalysisError("stale_identity")
            checkpoint("fetch")
            data = fetch_audio(
                get_audio_storage(checkpoint=lambda: checkpoint(None)),
                bucket=settings.UPLOAD_BUCKET_NAME,
                key=artifact.object_key,
                call_id=input.call_id,
                expected_version=artifact.object_version_or_etag,
            )
            checkpoint("decode")
            decoded = decode_audio(
                data,
                channel_index=provenance["target"]["channel_index"],
                expected_channels=artifact.channels,
            )
            del data
            validate_manifest(
                provenance,
                input.call_id,
                settings.UPLOAD_BUCKET_NAME,
                decoded_channels=decoded.original_channel_count,
            )
            if artifact.sha256 and artifact.sha256 != decoded.sha256:
                raise AudioDeterministicError("storage_unavailable")
            checkpoint("pitch")
            pitch, snr = analyze_pitch_snr(decoded.waveform, decoded.sample_rate_hz)
            env["metrics"]["average_pitch_hz"] = pitch
            checkpoint("snr")
            env["metrics"]["estimated_snr_db"] = snr
            checkpoint("vqi")
            if env["metrics"]["voice_quality_index"]["state"] == "pending":
                env["metrics"]["voice_quality_index"] = analyze_vqi(
                    decoded.waveform,
                    decoded.sample_rate_hz,
                    transport=provenance["transport"],
                    capture_origin=provenance["capture_origin"],
                )
            env["source"] = {
                "capture_origin": provenance["capture_origin"],
                "transport": provenance["transport"],
                "provider": provenance["system_engine"],
                "target_role": "tested_agent",
                "mapping_version": provenance["target"]["mapping_version"],
                "original_sample_rate_hz": decoded.sample_rate_hz,
                "original_channel_count": decoded.original_channel_count,
                "target_sample_count": len(decoded.waveform),
                "input_duration_seconds": len(decoded.waveform)
                / decoded.sample_rate_hz,
                "preprocessing_version": PREPROCESSING_VERSION,
            }
            env.update(
                state=derive_state(env["metrics"]),
                computed_at=datetime.now(UTC).isoformat(),
            )
            checkpoint("commit")
        except StaleAnalysisError:
            raise
        except AudioDeterministicError as exc:
            decoded = None
            env = _terminal_pending(
                original_env if exc.reason == "timeout" else env, exc.reason, exc.state
            )
        return _commit_result(input, env, provenance=provenance, decoded=decoded)
    except StaleAnalysisError as exc:
        activity.logger.info(
            "audio_metrics.stale_skip",
            extra={"analysis_id": input.analysis_id, "stale_reason": exc.reason},
        )
        return AnalyzeCallAudioOutput(False, "not_requested", {})
    finally:
        close_old_connections()


@activity.defn
async def analyze_call_audio(input: AnalyzeCallAudioInput) -> AnalyzeCallAudioOutput:
    import asyncio
    import threading
    import time

    from temporalio.exceptions import ApplicationError

    from ee.voice.services.audio_analysis.errors import AudioDeterministicError
    from tfc.temporal.common.heartbeat import Heartbeater
    from tfc.temporal.common.shutdown import ShutdownMonitor

    cancelled = threading.Event()
    started_at = time.perf_counter()
    last_stage = started_at
    log_context = {
        "call_id": input.call_id,
        "analysis_id": input.analysis_id,
        "org_id": input.org_id,
        "generation": input.generation,
        "attempt": activity.info().attempt,
    }
    deadline = datetime.fromisoformat(input.attempt_deadline_at.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ApplicationError(
            "Invalid analysis deadline", type="ValueError", non_retryable=True
        )
    async with Heartbeater(factor=4) as heartbeater, ShutdownMonitor() as monitor:

        def checkpoint(stage):
            nonlocal last_stage
            if cancelled.is_set():
                raise asyncio.CancelledError()
            monitor.raise_if_is_worker_shutdown()
            if datetime.now(UTC) >= deadline:
                raise AudioDeterministicError("timeout")
            if stage is None:
                return
            now = time.perf_counter()
            activity.logger.info(
                "audio_metrics.stage",
                extra={
                    **log_context,
                    "stage": stage,
                    "ms_since_previous_stage": (now - last_stage) * 1000,
                },
            )
            last_stage = now
            heartbeater.details = ({"stage": stage},)
            activity.heartbeat({"stage": stage})

        task = asyncio.create_task(asyncio.to_thread(_analyze, input, checkpoint))
        try:
            result = await asyncio.shield(task)
            activity.logger.info(
                "audio_metrics.committed"
                if result.committed
                else "audio_metrics.skipped",
                extra={
                    **log_context,
                    "state": result.state,
                    "reasons": result.reasons,
                    "total_ms": (time.perf_counter() - started_at) * 1000,
                },
            )
            return result
        except asyncio.CancelledError:
            cancelled.set()
            # Wait for bounded DSP to leave its thread and release audio memory.
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            raise
        except Exception as exc:
            from botocore.exceptions import (
                ClientError,
                EndpointConnectionError,
                ReadTimeoutError,
            )
            from django.db import OperationalError
            from minio.error import InvalidResponseError, ServerError

            from ee.voice.services.audio_storage import is_storage_transport_error
            from tfc.temporal.common.shutdown import WorkerShuttingDownError

            retryable = is_storage_transport_error(exc) or isinstance(
                exc,
                (
                    OperationalError,
                    EndpointConnectionError,
                    ReadTimeoutError,
                    WorkerShuttingDownError,
                    InvalidResponseError,
                    ServerError,
                    TimeoutError,
                    ConnectionError,
                ),
            )
            if isinstance(exc, ClientError):
                retryable = (
                    exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                    >= 500
                )
            raise ApplicationError(
                "Audio analysis failed",
                type=type(exc).__name__,
                non_retryable=not retryable,
            ) from None


@activity.defn
async def schedule_audio_analysis(
    input: ScheduleAudioAnalysisInput,
) -> ScheduleAudioAnalysisOutput:
    from asgiref.sync import sync_to_async

    return await sync_to_async(_schedule_audio_analysis)(input)


@activity.defn
async def finalize_audio_analysis_failure(input: FinalizeFailureInput) -> None:
    from asgiref.sync import sync_to_async

    await sync_to_async(_finalize_audio_analysis_failure)(input)
