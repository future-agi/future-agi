from __future__ import annotations

import hashlib
import tempfile
from datetime import timedelta
from typing import Any, BinaryIO

from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.models import (
    CallExecution,
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
    canonical_digest,
    canonical_json_bytes,
    update_execution_counts,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

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
                for start, end in _missing_ranges(
                    watermark + 1, sorted(sequences)
                )
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
    call.status = {
        "passed": CallExecution.CallStatus.COMPLETED,
        "failed": CallExecution.CallStatus.FAILED,
        "errored": CallExecution.CallStatus.FAILED,
        "skipped": CallExecution.CallStatus.CANCELLED,
    }[body["status"]]
    call_data = body.get("call")
    if call_data:
        call.started_at = call_data["started_at"]
        call.completed_at = call_data["ended_at"]
        call.duration_seconds = round(call_data["duration_ms"] / 1000)
    elif body["status"] == "skipped":
        call.completed_at = timezone.now()
    metadata = dict(call.call_metadata or {})
    metadata["hosted_harness_receipt"] = _json_ready(body)
    call.call_metadata = metadata
    if body.get("failure"):
        call.error_message = body["failure"]["message"]
    call.save(
        update_fields=[
            "status",
            "started_at",
            "completed_at",
            "duration_seconds",
            "call_metadata",
            "error_message",
            "updated_at",
        ]
    )


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
