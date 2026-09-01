from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import timedelta
from typing import Any, BinaryIO

from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.models import (
    AgentDefinition,
    CallExecution,
    CallTranscript,
    HostedHarnessArtifact,
    HostedHarnessAttempt,
    HostedHarnessEvent,
    HostedHarnessJob,
    HostedHarnessManifest,
    HostedHarnessReceipt,
    HostedHarnessScenario,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    _resolve_scenario_modality,
    canonical_digest,
    canonical_json_bytes,
    update_execution_counts,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_object_url, get_storage_client

logger = logging.getLogger(__name__)

_EVENT_TYPES = {
    "stage_changed",
    "parallelism_degraded",
    "baseline_frozen",
    "baseline_inputs_changed",
    "world_unhealthy",
    "scenario_started",
    "scenario_retried",
    "log",
    "terminal",
}
_TERMINAL_STAGES = {"completed", "failed", "canceled"}
_ARTIFACT_KINDS = {
    "recording_combined",
    "recording_stereo",
    "recording_customer",
    "recording_assistant",
    "transcript",
    "tool_trace",
    "result",
    "build",
    "trace",
    "log",
    "other",
}
_ALLOWED_ARTIFACTS = {
    "metadata-only": {"build", "result", "log"},
    "traces": {"build", "result", "log", "trace", "tool_trace", "transcript"},
    "traces-and-recordings": _ARTIFACT_KINDS - {"other"},
    "full": _ARTIFACT_KINDS,
}
_GAP_TIMEOUT = timedelta(seconds=60)


_RECORDING_ARTIFACT_KINDS = {
    "recording_combined",
    "recording_stereo",
    "recording_customer",
    "recording_assistant",
}


def _normalized_artifact_content_type(
    *, kind: str, supplied: str, header: bytes
) -> str:
    """Correct provably-wrong recording MIME types before object storage.

    The hosted channel historically defaulted every recording to MP4 while
    LiveKit call artifacts were RIFF/WAVE.  Object storage preserves the
    supplied MIME type, so browsers later refused to decode the recording.
    Only normalize recognizable media signatures; otherwise retain the
    sender-provided value for forward compatibility.
    """
    if kind not in _RECORDING_ARTIFACT_KINDS:
        return supplied
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    return supplied


def ingest_event_batch(
    attempt: HostedHarnessAttempt, events: list[dict[str, Any]]
) -> dict[str, Any]:
    rejected: list[dict[str, Any]] = []
    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=attempt.id)
        )
        _assert_current_attempt(attempt)
        for event in events:
            rejection = _validate_event(attempt, event)
            if rejection:
                rejected.append(rejection)
            _store_event(attempt, event, rejection)
        _advance_event_watermark(attempt)
        attempt.heartbeat_at = timezone.now()
        attempt.save(
            update_fields=[
                "event_watermark",
                "gap_started_at",
                "released_event_gaps",
                "heartbeat_at",
                "terminal_stage",
                "terminal_reason",
                "terminal_failure",
                "terminal_event_received",
                "state",
                "updated_at",
            ]
        )
        return {
            "acked_through_sequence": attempt.event_watermark,
            "rejected": rejected,
        }


def ingest_result_receipt(
    attempt: HostedHarnessAttempt, body: dict[str, Any]
) -> tuple[HostedHarnessReceipt, bool]:
    supplied_digest = body["digest"]
    canonical = {key: value for key, value in body.items() if key != "digest"}
    if canonical_digest(canonical) != supplied_digest:
        raise HostedHarnessError(
            "digest_mismatch", "result receipt digest did not match", status_code=422
        )
    _assert_body_binding(attempt, body)
    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=attempt.id)
        )
        _assert_current_attempt(attempt)
        try:
            registration = (
                HostedHarnessScenario.no_workspace_objects.select_related(
                    "call_execution"
                )
                .select_for_update(of=("self",))
                .get(job=attempt.job, scenario_key=body["scenario_key"])
            )
        except HostedHarnessScenario.DoesNotExist as exc:
            raise HostedHarnessError(
                "scenario_unknown", "scenario_key is not registered", status_code=404
            ) from exc
        if str(registration.scenario_id) != str(body["scenario_id"]):
            raise HostedHarnessError(
                "scenario_mismatch",
                "scenario_id does not match scenario_key",
                status_code=403,
            )
        _assert_receipt_artifacts(attempt.job, body)
        existing = (
            HostedHarnessReceipt.no_workspace_objects.select_for_update()
            .filter(job=attempt.job, scenario=registration)
            .first()
        )
        if existing:
            if existing.digest == supplied_digest:
                # Receipt delivery is idempotent, but the platform projection may
                # have been created before a late authoring output (notably the
                # contract modality) was synchronized.  Re-applying the same
                # sealed receipt is safe and repairs that derived state without
                # requiring another customer call.
                _apply_receipt_to_call(registration, body)
                update_execution_counts(attempt.job)
                return existing, False
            if existing.attempt_number >= attempt.attempt_number:
                raise HostedHarnessError(
                    "receipt_conflict",
                    "a different receipt is already accepted for this scenario",
                    status_code=409,
                )
            # BaseModel.delete() is a soft delete, so delete-then-create still violates the
            # one-latest-receipt-per-job/scenario database constraint. Replace the older attempt
            # atomically in place instead.
            existing.attempt = attempt
            existing.attempt_number = attempt.attempt_number
            existing.digest = supplied_digest
            existing.status = body["status"]
            existing.body = _json_ready(body)
            existing.save(
                update_fields=[
                    "attempt",
                    "attempt_number",
                    "digest",
                    "status",
                    "body",
                    "updated_at",
                ]
            )
            receipt = existing
        else:
            receipt = HostedHarnessReceipt.no_workspace_objects.create(
                job=attempt.job,
                attempt=attempt,
                scenario=registration,
                attempt_number=attempt.attempt_number,
                digest=supplied_digest,
                status=body["status"],
                body=_json_ready(body),
            )
        _apply_receipt_to_call(registration, body)
        update_execution_counts(attempt.job)
        return receipt, True


def ingest_artifact(
    attempt: HostedHarnessAttempt,
    *,
    digest: str,
    kind: str,
    size: int,
    content_type: str,
    scenario_key: str | None,
    stream: BinaryIO,
) -> tuple[HostedHarnessArtifact, bool]:
    digest = digest.lower()
    if kind not in _ARTIFACT_KINDS:
        raise HostedHarnessError(
            "artifact_kind_unknown",
            f"unsupported artifact kind: {kind}",
            status_code=422,
        )
    if size < 0:
        raise HostedHarnessError(
            "size_mismatch", "artifact size must be non-negative", status_code=422
        )
    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=attempt.id)
        )
        _assert_current_attempt(attempt)
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        if kind not in _ALLOWED_ARTIFACTS[job.artifact_level]:
            raise HostedHarnessError(
                "artifact_level_forbidden",
                f"{kind} is forbidden at artifact level {job.artifact_level}",
                status_code=422,
            )
        existing = HostedHarnessArtifact.no_workspace_objects.filter(
            job=job, sha256=digest
        ).first()
        if existing:
            if existing.size != size or existing.kind != kind:
                raise HostedHarnessError(
                    "artifact_conflict",
                    "artifact digest already exists with different metadata",
                    status_code=409,
                )
            return existing, False
        absolute_budget = int(job.max_artifact_bytes * 1.1)
        if job.uploaded_artifact_bytes + size > absolute_budget:
            raise HostedHarnessError(
                "artifact_budget_exceeded",
                "artifact exceeds the remaining job upload budget",
                status_code=413,
            )

    temporary = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    actual_size = 0
    hasher = hashlib.sha256()
    while True:
        chunk = stream.read(min(1024 * 1024, size - actual_size + 1))
        if not chunk:
            break
        actual_size += len(chunk)
        if actual_size > size:
            break
        hasher.update(chunk)
        temporary.write(chunk)
    if actual_size != size:
        temporary.close()
        raise HostedHarnessError(
            "size_mismatch",
            "artifact size did not match X-Artifact-Size",
            status_code=422,
        )
    if hasher.hexdigest() != digest:
        temporary.close()
        raise HostedHarnessError(
            "digest_mismatch",
            "artifact bytes did not match URL digest",
            status_code=422,
        )
    temporary.seek(0)
    content_type = _normalized_artifact_content_type(
        kind=kind, supplied=content_type, header=temporary.read(12)
    )
    temporary.seek(0)
    object_key = f"alk-harness/{attempt.job.organization_id}/{attempt.job_id}/{digest}"
    get_storage_client().put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=object_key,
        data=temporary,
        length=size,
        content_type=content_type,
    )
    temporary.close()

    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        try:
            artifact = HostedHarnessArtifact.no_workspace_objects.create(
                job=job,
                sha256=digest,
                kind=kind,
                size=size,
                content_type=content_type,
                object_key=object_key,
                scenario_key=scenario_key,
            )
        except IntegrityError:
            artifact = HostedHarnessArtifact.no_workspace_objects.get(
                job=job, sha256=digest
            )
            return artifact, False
        job.uploaded_artifact_bytes += size
        job.save(update_fields=["uploaded_artifact_bytes", "updated_at"])
        return artifact, True


def ingest_manifest(
    attempt: HostedHarnessAttempt, body: dict[str, Any]
) -> tuple[HostedHarnessManifest, bool]:
    supplied_digest = body["digest"]
    canonical = {key: value for key, value in body.items() if key != "digest"}
    if canonical_digest(canonical) != supplied_digest:
        raise HostedHarnessError(
            "digest_mismatch", "manifest digest did not match", status_code=422
        )
    _assert_body_binding(attempt, body)
    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=attempt.id)
        )
        _assert_current_attempt(attempt)
        if not attempt.terminal_event_received:
            raise HostedHarnessError(
                "terminal_event_required",
                "manifest is accepted only after the terminal event",
                status_code=409,
            )
        if not body["complete"] and attempt.terminal_stage != "canceled":
            raise HostedHarnessError(
                "manifest_incomplete",
                "only canceled attempts may submit an incomplete manifest",
                status_code=422,
            )
        existing = HostedHarnessManifest.no_workspace_objects.filter(
            attempt=attempt, digest=supplied_digest
        ).first()
        if existing:
            return existing, False
        for entry in body["entries"]:
            artifact_id = entry["artifact_id"].removeprefix("sha256:")
            artifact = HostedHarnessArtifact.no_workspace_objects.filter(
                job=attempt.job,
                sha256=artifact_id,
                size=entry["size"],
                kind=entry["kind"],
            ).first()
            if artifact is None:
                raise HostedHarnessError(
                    "artifact_unknown",
                    f"manifest artifact is not acknowledged: {entry['artifact_id']}",
                    status_code=422,
                )
        if body["complete"]:
            present_kinds = {entry["kind"] for entry in body["entries"]}
            missing_kinds = {"build", "result", "log"} - present_kinds
            if missing_kinds:
                raise HostedHarnessError(
                    "manifest_required_artifact_missing",
                    "manifest is missing required artifact kinds: "
                    + ", ".join(sorted(missing_kinds)),
                    status_code=422,
                )
        manifest = HostedHarnessManifest.no_workspace_objects.create(
            attempt=attempt,
            digest=supplied_digest,
            complete=body["complete"],
            body=_json_ready(body),
        )
        attempt.manifest_acked = True
        attempt.state = HostedHarnessAttempt.State.CLEANING_UP
        attempt.save(update_fields=["manifest_acked", "state", "updated_at"])
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        _backfill_missing_receipts(attempt)
        update_execution_counts(job)
        job.state = HostedHarnessJob.State.CLEANING_UP
        job.current_stage = "cleaning_up"
        job.save(update_fields=["state", "current_stage", "updated_at"])
        return manifest, True


def _validate_event(
    attempt: HostedHarnessAttempt, event: dict[str, Any]
) -> dict[str, Any] | None:
    def reject(code: str, message: str) -> dict[str, Any]:
        return {
            "event_id": event["event_id"],
            "sequence": event["sequence"],
            "code": code,
            "message": message,
        }

    try:
        _assert_body_binding(attempt, event)
    except HostedHarnessError as exc:
        return reject(exc.code, exc.message)
    if len(canonical_json_bytes(event["payload"])) > 32 * 1024:
        return reject("event_payload_too_large", "event payload exceeds 32 KB")
    if event["type"] not in _EVENT_TYPES:
        return reject("event_type_unknown", f"unknown event type: {event['type']}")
    if canonical_digest(event["payload"]) != event["digest"]:
        return reject("digest_mismatch", "event payload digest did not match")
    payload_error = _event_payload_error(
        event["type"], event["stage"], event["payload"]
    )
    if payload_error:
        return reject("event_payload_invalid", payload_error)
    existing_id = HostedHarnessEvent.no_workspace_objects.filter(
        event_id=event["event_id"]
    ).first()
    if existing_id and (
        existing_id.attempt_id != attempt.id
        or existing_id.sequence != event["sequence"]
        or existing_id.digest != event["digest"]
    ):
        return reject("event_id_conflict", "event_id already identifies another event")
    existing_sequence = HostedHarnessEvent.no_workspace_objects.filter(
        attempt=attempt, sequence=event["sequence"]
    ).first()
    if existing_sequence and existing_sequence.event_id != event["event_id"]:
        return reject(
            "event_sequence_conflict", "sequence already identifies another event"
        )
    if event["type"] == "terminal" and attempt.terminal_event_received:
        if existing_id is None:
            return reject("terminal_conflict", "attempt already has a terminal event")
    return None


def _store_event(
    attempt: HostedHarnessAttempt,
    event: dict[str, Any],
    rejection: dict[str, Any] | None,
) -> None:
    if HostedHarnessEvent.no_workspace_objects.filter(
        event_id=event["event_id"]
    ).exists():
        return
    HostedHarnessEvent.no_workspace_objects.create(
        event_id=event["event_id"],
        attempt=attempt,
        sequence=event["sequence"],
        stage=event["stage"],
        event_type=event["type"],
        payload=_json_ready(event["payload"]) if rejection is None else None,
        digest=event["digest"],
        emitted_at=event["emitted_at"],
        accepted=rejection is None,
        rejection_code=rejection and rejection["code"],
        rejection_message=rejection and rejection["message"],
    )
    if rejection is None and event["type"] == "stage_changed":
        attempt.job.current_stage = event["payload"]["to"]
        attempt.job.save(update_fields=["current_stage", "updated_at"])
    if rejection is None and event["type"] == "scenario_started":
        # Registrations and their CallExecution rows are allocated before the guest starts.
        # Project the guest's lifecycle event into the existing platform row so the simulation
        # UI shows an active call instead of leaving it PENDING until the terminal receipt.  The
        # receipt remains authoritative for the provider's exact started_at/ended_at timestamps.
        registration = (
            HostedHarnessScenario.no_workspace_objects.select_related("call_execution")
            .filter(
                job=attempt.job,
                scenario_key=event["payload"]["scenario_key"],
            )
            .first()
        )
        if registration and registration.call_execution_id:
            CallExecution.objects.filter(
                id=registration.call_execution_id,
                status=CallExecution.CallStatus.PENDING,
            ).update(status=CallExecution.CallStatus.ONGOING)
    if rejection is None and event["type"] == "terminal":
        payload = event["payload"]
        attempt.terminal_stage = payload["stage"]
        attempt.terminal_reason = payload.get("reason")
        attempt.terminal_failure = payload.get("failure")
        attempt.terminal_event_received = True
        attempt.state = HostedHarnessAttempt.State.FINALIZING


def _advance_event_watermark(attempt: HostedHarnessAttempt) -> None:
    sequences = set(
        HostedHarnessEvent.no_workspace_objects.filter(
            attempt=attempt, sequence__gt=attempt.event_watermark
        ).values_list("sequence", flat=True)
    )
    watermark = attempt.event_watermark
    while watermark + 1 in sequences:
        watermark += 1
    if sequences and max(sequences) > watermark:
        now = timezone.now()
        if attempt.gap_started_at is None:
            attempt.gap_started_at = now
        elif now - attempt.gap_started_at >= _GAP_TIMEOUT:
            gaps = list(attempt.released_event_gaps or [])
            gaps.extend(
                {
                    "from": start,
                    "through": end,
                    "released_at": now.isoformat(),
                }
                for start, end in _missing_ranges(watermark + 1, sorted(sequences))
            )
            attempt.released_event_gaps = gaps
            watermark = max(sequences)
            attempt.gap_started_at = None
    else:
        attempt.gap_started_at = None
    attempt.event_watermark = watermark


def _missing_ranges(start: int, received: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    for sequence in received:
        if sequence < cursor:
            continue
        if sequence > cursor:
            ranges.append((cursor, sequence - 1))
        cursor = sequence + 1
    return ranges


def _event_payload_error(event_type: str, stage: str, payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "payload must be an object"
    if event_type == "stage_changed":
        if set(payload) != {"from", "to"} or payload["to"] != stage:
            return "stage_changed requires exactly from/to and stage == to"
    elif event_type == "parallelism_degraded":
        if set(payload) != {"requested", "effective", "reason"}:
            return "parallelism_degraded requires requested/effective/reason"
        if payload["reason"] not in {"conformance_gate_failed", "fixed_port"}:
            return "invalid parallelism degradation reason"
        if not (
            isinstance(payload["requested"], int)
            and isinstance(payload["effective"], int)
        ):
            return "parallelism values must be integers"
        if not 1 <= payload["effective"] < payload["requested"]:
            return "effective parallelism must be lower than requested"
    elif event_type == "terminal":
        if (
            payload.get("stage") not in _TERMINAL_STAGES
            or payload.get("stage") != stage
        ):
            return "terminal stage must match event stage"
        if payload.get("reason") not in {None, "ttl_exceeded", "user_canceled"}:
            return "invalid terminal reason"
    elif event_type == "log":
        if payload.get("level") not in {"debug", "info", "warning", "error"}:
            return "invalid log level"
        if not isinstance(payload.get("message"), str):
            return "log message must be a string"
    return None


def _assert_body_binding(attempt: HostedHarnessAttempt, body: dict[str, Any]) -> None:
    if (
        str(body["job_id"]) != str(attempt.job_id)
        or str(body["attempt_id"]) != str(attempt.id)
        or body["attempt_number"] != attempt.attempt_number
    ):
        raise HostedHarnessError(
            "attempt_mismatch",
            "request body does not match the authenticated attempt",
            status_code=403,
        )


def _assert_current_attempt(attempt: HostedHarnessAttempt) -> None:
    if attempt.attempt_number < attempt.job.current_attempt_number:
        raise HostedHarnessError(
            "attempt_superseded", "attempt has been superseded", status_code=409
        )
    if timezone.now() >= attempt.expires_at:
        raise HostedHarnessError(
            "attempt_expired", "attempt token has expired", status_code=401
        )


def _assert_receipt_artifacts(job: HostedHarnessJob, body: dict[str, Any]) -> None:
    call = body.get("call") or {}
    artifact_ids = list(call.get("recording_artifacts") or [])
    if call.get("transcript_artifact"):
        artifact_ids.append(call["transcript_artifact"])
    known = set(
        HostedHarnessArtifact.no_workspace_objects.filter(
            job=job,
            sha256__in=[item.removeprefix("sha256:") for item in artifact_ids],
        ).values_list("sha256", flat=True)
    )
    missing = [
        item for item in artifact_ids if item.removeprefix("sha256:") not in known
    ]
    if missing:
        raise HostedHarnessError(
            "artifact_unknown",
            f"receipt references unacknowledged artifact: {missing[0]}",
            status_code=422,
        )


def _apply_receipt_to_call(
    registration: HostedHarnessScenario, body: dict[str, Any]
) -> None:
    call = registration.call_execution
    if call is None:
        raise HostedHarnessError(
            "scenario_not_begun",
            "scenario has no pre-allocated execution",
            status_code=409,
        )
    call.status = _call_lifecycle_status(body)
    call_data = body.get("call")
    resolved_modality = _resolve_scenario_modality(registration.job, body)
    if call_data and call_data.get("recording_artifacts"):
        # A persisted audio recording is definitive evidence of a voice call,
        # even if scenario provisioning raced ahead of contract synchronization.
        resolved_modality = CallExecution.SimulationCallType.VOICE
    call.simulation_call_type = resolved_modality
    if call_data:
        call.started_at = call_data["started_at"]
        call.ended_at = call_data["ended_at"]
        call.completed_at = call_data["ended_at"]
        call.duration_seconds = round(call_data["duration_ms"] / 1000)
    elif body["status"] == "skipped":
        call.completed_at = timezone.now()
    metadata = dict(call.call_metadata or {})
    metadata["hosted_harness_receipt"] = _json_ready(body)
    metadata["harness_evaluations"] = _receipt_evaluations(body)
    metadata["harness_outcome_status"] = body["status"]
    coverage = _receipt_evaluation_coverage(body)
    metadata["harness_eval_coverage"] = coverage
    if coverage["complete"]:
        metadata["harness_grading_status"] = "completed"
        metadata.pop("harness_grading_error", None)
    else:
        metadata["harness_grading_status"] = "failed"
        metadata["harness_grading_error"] = (
            "Required evaluation coverage was incomplete "
            f"({coverage['executed']}/{coverage['expected']} executed). "
            "This is a grading failure, not an agent failure."
        )
    update_fields = [
        "status",
        "simulation_call_type",
        "started_at",
        "ended_at",
        "completed_at",
        "duration_seconds",
        "call_metadata",
        "error_message",
        "updated_at",
    ]
    if call_data:
        artifact_ids = list(call_data.get("recording_artifacts") or [])
        transcript_id = call_data.get("transcript_artifact")
        if transcript_id:
            artifact_ids.append(transcript_id)
        artifacts = list(
            HostedHarnessArtifact.no_workspace_objects.filter(
                job=registration.job,
                sha256__in=[item.removeprefix("sha256:") for item in artifact_ids],
            )
        )
        tool_trace = (
            HostedHarnessArtifact.no_workspace_objects.filter(
                job=registration.job,
                scenario_key=registration.scenario_key,
                kind="tool_trace",
            )
            .order_by("created_at")
            .last()
        )
        if tool_trace is not None and all(
            artifact.id != tool_trace.id for artifact in artifacts
        ):
            artifacts.append(tool_trace)
        metadata["hosted_harness_artifacts"] = {
            artifact.kind: {
                "sha256": artifact.sha256,
                "object_key": artifact.object_key,
                "content_type": artifact.content_type,
                "url": get_object_url(UPLOAD_BUCKET_NAME, artifact.object_key),
            }
            for artifact in artifacts
        }
        combined = next(
            (item for item in artifacts if item.kind == "recording_combined"), None
        )
        stereo = next(
            (item for item in artifacts if item.kind == "recording_stereo"), None
        )
        if combined is not None:
            call.recording_url = get_object_url(UPLOAD_BUCKET_NAME, combined.object_key)
            update_fields.append("recording_url")
        if stereo is not None:
            call.stereo_recording_url = get_object_url(
                UPLOAD_BUCKET_NAME, stereo.object_key
            )
            update_fields.append("stereo_recording_url")
        if combined is not None or stereo is not None:
            call.recording_available = True
            update_fields.append("recording_available")

        transcript = next(
            (item for item in artifacts if item.kind == "transcript"), None
        )
        if transcript is not None:
            _ingest_hosted_transcript(call, transcript)
            call.transcript_available = True
            update_fields.append("transcript_available")
        if tool_trace is not None:
            provider_data = dict(call.provider_call_data or {})
            livekit_data = dict(provider_data.get("livekit") or {})
            livekit_data["tool_calls"] = _read_hosted_tool_trace(tool_trace)
            provider_data["livekit"] = livekit_data
            call.provider_call_data = provider_data
            update_fields.append("provider_call_data")
    call.call_metadata = metadata
    from simulate.services.alk_simulate_ingestion import (
        _apply_conversation_metrics,
        _apply_harness_evaluation_outputs,
        _dispatch_csat_once,
    )

    _apply_harness_evaluation_outputs(call)
    update_fields.append("eval_outputs")
    # The same CallExecution row is intentionally reused across user-triggered reruns so the old
    # result remains visible until its replacement receipt arrives.  Once that receipt lands it
    # is authoritative for this row: never leave an earlier attempt's transport failure attached
    # to a later completed call.
    call.error_message = ""
    if body.get("failure"):
        call.error_message = body["failure"]["message"]
    # A sealed voice call stores its transcript, but the hosted path never
    # derived the conversation analytics the non-hosted voice flow computes
    # (turn/message counts, talk ratio, WPM, interruptions, agent latency,
    # token usage), leaving call-details empty.  Compute them from the stored
    # transcript; keep it best-effort so a metrics error never fails ingestion.
    if call.simulation_call_type == CallExecution.SimulationCallType.VOICE:
        try:
            _apply_conversation_metrics(call)
        except Exception:  # noqa: BLE001 - metrics are best-effort, never fatal
            logger.warning(
                "hosted conversation metrics failed for call %s",
                call.id,
                exc_info=True,
            )
        else:
            update_fields.extend(
                [
                    "avg_agent_latency_ms",
                    "user_interruption_count",
                    "user_interruption_rate",
                    "ai_interruption_count",
                    "ai_interruption_rate",
                    "user_wpm",
                    "bot_wpm",
                    "talk_ratio",
                    "avg_stop_time_after_interruption_ms",
                    "message_count",
                    "conversation_metrics_data",
                    "duration_seconds",
                ]
            )
    call.save(update_fields=list(dict.fromkeys(update_fields)))
    if resolved_modality == CallExecution.SimulationCallType.VOICE:
        _ensure_run_agent_is_voice(registration.job)
    if call.status == CallExecution.CallStatus.COMPLETED:
        call_id = call.id
        transaction.on_commit(
            lambda: _dispatch_csat_once(CallExecution.objects.get(id=call_id))
        )


def _ensure_run_agent_is_voice(job: HostedHarnessJob) -> None:
    """Promote the run's agent definition to voice from definitive evidence.

    ``_resolve_scenario_modality`` runs at scenario-provision time, before the
    authoring contract is guaranteed persisted on the job, so a voice run can
    register a ``text`` agent definition and then render through the chat
    schema -- effectively disappearing from call-details.  A sealed recording
    is ground truth and arrives with the receipt, so correct the definition
    here (idempotent; text runs never reach this path).
    """
    run_test = getattr(job, "run_test", None)
    if run_test is None:
        return
    agent = run_test.agent_definition
    if agent is not None and agent.agent_type != AgentDefinition.AgentTypeChoices.VOICE:
        agent.agent_type = AgentDefinition.AgentTypeChoices.VOICE
        agent.save(update_fields=["agent_type", "updated_at"])


def _call_lifecycle_status(body: dict[str, Any]) -> str:
    """Keep call transport lifecycle separate from harness/eval outcome.

    A scenario can be ``failed`` or ``errored`` after a real call completed
    (for example, missing tool evidence).  When the sealed receipt contains a
    completed call interval, the call row must remain playable/completed and
    the outcome stays in ``harness_outcome_status`` and failure metadata.
    """
    outcome = body["status"]
    if outcome == "skipped":
        return CallExecution.CallStatus.CANCELLED
    call = body.get("call")
    if isinstance(call, dict) and call.get("started_at") and call.get("ended_at"):
        return CallExecution.CallStatus.COMPLETED
    if outcome in {"passed", "failed"}:
        return CallExecution.CallStatus.COMPLETED
    return CallExecution.CallStatus.FAILED


def _receipt_evaluations(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert hosted receipt checks to the platform's existing eval-output shape."""
    results: list[dict[str, Any]] = []
    for goal in body.get("sub_goals") or []:
        if not isinstance(goal, dict) or not goal.get("name"):
            continue
        results.append(
            {
                "name": str(goal["name"]),
                "kind": "judge" if goal.get("judged") else "checkpoint",
                "passed": bool(goal.get("held")),
                "reason": str(goal.get("reason") or ""),
            }
        )
    for evaluation in body.get("evaluations") or []:
        if isinstance(evaluation, dict):
            results.append(dict(evaluation))
    return results


def _receipt_evaluation_coverage(body: dict[str, Any]) -> dict[str, int | bool]:
    """Describe whether every declared hosted judgement produced a verdict."""
    sub_goals = [item for item in body.get("sub_goals") or [] if isinstance(item, dict)]
    evaluations = [
        item for item in body.get("evaluations") or [] if isinstance(item, dict)
    ]
    expected = len(sub_goals) + len(evaluations)
    executed = sum(item.get("held") is not None for item in sub_goals) + sum(
        not bool(item.get("grading_error")) for item in evaluations
    )
    return {
        "expected": expected,
        "executed": executed,
        "failed": max(0, expected - executed),
        "complete": executed == expected,
    }


def _read_hosted_tool_trace(artifact: HostedHarnessArtifact) -> list[dict[str, Any]]:
    """Read the sealed JSONL tool trace into the authorized call detail payload."""
    response = None
    try:
        response = get_storage_client().get_object(
            UPLOAD_BUCKET_NAME, artifact.object_key
        )
        raw = response.read().decode("utf-8")
        calls: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                calls.append(value)
        return calls
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _ingest_hosted_transcript(
    call: CallExecution, artifact: HostedHarnessArtifact
) -> None:
    """Materialize the sealed transcript artifact into the normal call transcript model.

    The v2 producer emits structured JSON.  Raw text remains supported for artifacts uploaded by
    older guests, so upgrading the platform does not invalidate already-running attempts.
    """
    response = None
    try:
        response = get_storage_client().get_object(
            UPLOAD_BUCKET_NAME, artifact.object_key
        )
        raw = response.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"transcript": raw, "messages": []}
        messages = payload.get("messages") if isinstance(payload, dict) else []
        rows: list[CallTranscript] = []
        if isinstance(messages, list):
            # The v2 transcript carries absolute speech timing
            # (``started_speaking_at`` / ``stopped_speaking_at`` in epoch
            # seconds).  Anchor to the earliest turn so CallTranscript stores
            # real per-turn offsets in ms: conversation metrics (talk ratio,
            # WPM, agent latency, interruptions) are all derived from these and
            # collapse to zero when every turn shares the row index.
            speech_starts = [
                message["started_speaking_at"]
                for message in messages
                if isinstance(message, dict)
                and isinstance(message.get("started_speaking_at"), (int, float))
            ]
            base_time = min(speech_starts) if speech_starts else None
            valid_roles = {
                choice for choice, _label in CallTranscript.SpeakerRole.choices
            }
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if content is None:
                    continue
                role = str(message.get("role") or "unknown")
                started = message.get("started_speaking_at")
                stopped = message.get("stopped_speaking_at")
                if base_time is not None and isinstance(started, (int, float)):
                    start_ms = int(round((started - base_time) * 1000))
                    end_ms = (
                        int(round((stopped - base_time) * 1000))
                        if isinstance(stopped, (int, float))
                        else start_ms
                    )
                else:
                    # Older guests emit no timing; preserve turn order only.
                    start_ms = index
                    end_ms = index
                rows.append(
                    CallTranscript(
                        call_execution=call,
                        speaker_role=role if role in valid_roles else "unknown",
                        content=str(content),
                        start_time_ms=start_ms,
                        end_time_ms=end_ms,
                    )
                )
        if not rows and isinstance(payload, dict) and payload.get("transcript"):
            rows.append(
                CallTranscript(
                    call_execution=call,
                    speaker_role=CallTranscript.SpeakerRole.UNKNOWN,
                    content=str(payload["transcript"]),
                )
            )
        CallTranscript.objects.filter(call_execution=call).delete()
        if rows:
            CallTranscript.objects.bulk_create(rows)
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _backfill_missing_receipts(attempt: HostedHarnessAttempt) -> None:
    existing = set(
        HostedHarnessReceipt.no_workspace_objects.filter(job=attempt.job).values_list(
            "scenario_id", flat=True
        )
    )
    registrations = HostedHarnessScenario.no_workspace_objects.filter(job=attempt.job)
    for registration in registrations:
        if registration.id in existing:
            continue
        body = {
            "schema_version": "futureagi.harness-result.v1",
            "job_id": str(attempt.job_id),
            "attempt_id": str(attempt.id),
            "attempt_number": attempt.attempt_number,
            "scenario_key": registration.scenario_key,
            "scenario_id": str(registration.scenario_id),
            "scenario_attempt": 1,
            "world_index": None,
            "status": "skipped",
            "sub_goals": [],
            "evaluations": [],
            "call": None,
            "failure": None,
        }
        digest = canonical_digest(body)
        body["digest"] = digest
        HostedHarnessReceipt.no_workspace_objects.create(
            job=attempt.job,
            attempt=attempt,
            scenario=registration,
            attempt_number=attempt.attempt_number,
            digest=digest,
            status="skipped",
            body=body,
        )
        _apply_receipt_to_call(registration, body)


def _json_ready(value: object) -> object:
    import json

    return json.loads(canonical_json_bytes(value))
