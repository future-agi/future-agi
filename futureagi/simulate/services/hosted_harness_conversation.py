from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import secrets
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    HostedHarnessAttempt,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessConversationTranscript,
    HostedHarnessJob,
    HostedHarnessReceipt,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    hash_secret,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

CONVERSATION_SCHEMA_VERSION = "futureagi.harness-conversation.v1"
EVENT_SCHEMA_VERSION = "futureagi.harness-conversation-event.v1"
WORKSPACE_MAX_BYTES = 256 * 1024 * 1024
COMMAND_LIMIT = 100
EVENT_LIMIT = 200
TRANSCRIPT_ENTRY_LIMIT = 20_000
TRANSCRIPT_BYTES_LIMIT = 32 * 1024 * 1024
logger = logging.getLogger(__name__)

# A chat process polls for commands every half second, even mid-turn; one that has not
# polled for this long is gone, whatever its provider still reports.
RUNTIME_STALE_SECONDS = 90
_HEARTBEAT_WRITE_SECONDS = 5
# Restarts that keep failing back off, then give up so the user is not left waiting forever.
RUNTIME_RETRY_BACKOFF_SECONDS = 60
RUNTIME_GIVE_UP_SECONDS = 15 * 60
# Only a runtime start writes a starting lease, so one this fresh is a start still in progress.
RUNTIME_START_SECONDS = 10 * 60
_TERMINAL_JOB_STATES = frozenset(
    {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
)
_ACTIVE_CONVERSATION_STATES = (
    HostedHarnessConversation.State.STARTING,
    HostedHarnessConversation.State.HYDRATING,
    HostedHarnessConversation.State.RESPONDING,
)


@dataclass(frozen=True)
class ConversationCapability:
    lease: HostedHarnessConversationLease
    token: str
    fence: str
    document: dict[str, Any]


def serialize_conversation(
    conversation: HostedHarnessConversation,
) -> dict[str, Any]:
    messages = list(reversed(conversation.messages.order_by("-sequence")[:EVENT_LIMIT]))
    events = list(reversed(conversation.events.order_by("-sequence")[:EVENT_LIMIT]))
    job_metadata = (conversation.job.payload or {}).get("metadata") or {}
    lease = HostedHarnessConversationLease.no_workspace_objects.filter(
        conversation=conversation
    ).first()
    # A live job always accepts messages: they wait in order until its sandbox can run chat.
    chat_available = (
        conversation.state != HostedHarnessConversation.State.RETIRED
        and bool(
            conversation.job.state not in _TERMINAL_JOB_STATES
            or conversation.latest_workspace_object_key
            or job_metadata.get("authoring_object_key")
        )
    )
    return {
        "conversation_id": str(conversation.id),
        "job_id": str(conversation.job_id),
        "state": conversation.state,
        "stage": conversation.current_stage,
        "active_invocation_id": conversation.active_invocation_id,
        "blocking_input": conversation.blocking_input,
        "messages": [_serialize_message(message) for message in messages],
        "events": [_serialize_event(event) for event in events],
        "event_watermark": conversation.event_acked_through,
        "runtime": {
            "state": lease.state if lease else "cold",
            "warm_until": lease.expires_at.isoformat() if lease else None,
            "degraded": conversation.state == HostedHarnessConversation.State.DEGRADED,
            "available": chat_available,
        },
    }


def runtime_is_live(lease: HostedHarnessConversationLease | None, *, now=None) -> bool:
    now = now or timezone.now()
    return bool(
        lease is not None
        and lease.state == HostedHarnessConversationLease.State.ACTIVE
        and lease.expires_at > now + timedelta(seconds=60)
        and lease.heartbeat_at is not None
        and now - lease.heartbeat_at < timedelta(seconds=RUNTIME_STALE_SECONDS)
    )


def runtime_start_pending(
    lease: HostedHarnessConversationLease | None, *, now=None
) -> bool:
    """Whether another worker is still starting this conversation's runtime."""
    now = now or timezone.now()
    return bool(
        lease is not None
        and lease.state == HostedHarnessConversationLease.State.STARTING
        and lease.heartbeat_at is not None
        and now - lease.heartbeat_at < timedelta(seconds=RUNTIME_START_SECONDS)
    )


def _unacked_commands(conversation: HostedHarnessConversation):
    return conversation.messages.filter(
        role=HostedHarnessConversationMessage.Role.USER,
        sequence__gt=conversation.command_acked_through,
        state__in=(
            HostedHarnessConversationMessage.State.QUEUED,
            HostedHarnessConversationMessage.State.DELIVERED,
        ),
    )


def settle_interrupted_turn(conversation: HostedHarnessConversation) -> None:
    """Close a reply that a dead chat process was streaming; its command is redelivered."""
    conversation.messages.filter(
        role=HostedHarnessConversationMessage.Role.ASSISTANT,
        state=HostedHarnessConversationMessage.State.STREAMING,
    ).update(
        state=HostedHarnessConversationMessage.State.FAILED,
        updated_at=timezone.now(),
    )
    if not _unacked_commands(conversation).exists():
        conversation.active_invocation_id = None


def recover_stalled_conversations(
    schedule: Callable[[str], Any], *, limit: int = 100
) -> dict[str, int]:
    """Restart chat runtimes that died with messages outstanding; settle ones that died idle."""
    now = timezone.now()
    unacked = HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation=OuterRef("pk"),
        role=HostedHarnessConversationMessage.Role.USER,
        sequence__gt=OuterRef("command_acked_through"),
        state__in=(
            HostedHarnessConversationMessage.State.QUEUED,
            HostedHarnessConversationMessage.State.DELIVERED,
        ),
    )
    healthy = HostedHarnessConversationLease.no_workspace_objects.filter(
        conversation=OuterRef("pk"),
    ).filter(
        Q(
            state=HostedHarnessConversationLease.State.ACTIVE,
            expires_at__gt=now + timedelta(seconds=60),
            heartbeat_at__gt=now - timedelta(seconds=RUNTIME_STALE_SECONDS),
        )
        | Q(
            state=HostedHarnessConversationLease.State.STARTING,
            heartbeat_at__gt=now - timedelta(seconds=RUNTIME_START_SECONDS),
        )
    )
    candidates = list(
        HostedHarnessConversation.no_workspace_objects.annotate(
            outstanding=Exists(unacked),
            healthy=Exists(healthy),
            overdue=Exists(
                unacked.filter(
                    created_at__lt=now - timedelta(seconds=RUNTIME_GIVE_UP_SECONDS)
                )
            ),
        )
        .filter(Q(outstanding=True) | Q(state__in=_ACTIVE_CONVERSATION_STATES))
        .filter(healthy=False)
        .filter(
            Q(outstanding=False)
            | Q(overdue=True)
            | Q(updated_at__lte=now - timedelta(seconds=RUNTIME_RETRY_BACKOFF_SECONDS))
        )
        .order_by("last_activity_at")[:limit]
    )
    leases = {
        lease.conversation_id: lease
        for lease in HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation_id__in=[candidate.id for candidate in candidates]
        )
    }
    counts = {
        "candidates": len(candidates),
        "restarted": 0,
        "settled": 0,
        "abandoned": 0,
        "errors": 0,
    }
    for candidate in candidates:
        lease = leases.get(candidate.id)
        if runtime_is_live(lease, now=now) or runtime_start_pending(lease, now=now):
            continue
        if not candidate.outstanding:
            counts["settled"] += _settle_idle(candidate.id)
            continue
        oldest = _unacked_commands(candidate).order_by("sequence").first()
        if oldest is not None and now - oldest.created_at > timedelta(
            seconds=RUNTIME_GIVE_UP_SECONDS
        ):
            counts["abandoned"] += _abandon_outstanding(candidate.id)
            continue
        try:
            # Also back off starts waiting for an attempt to reach RUNNING.
            HostedHarnessConversation.no_workspace_objects.filter(
                id=candidate.id
            ).update(updated_at=now)
            schedule(str(candidate.id))
        except Exception:
            logger.exception(
                "conversation runtime scheduling failed conversation=%s", candidate.id
            )
            counts["errors"] += 1
            continue
        counts["restarted"] += 1
    return counts


def _settle_idle(conversation_id) -> int:
    with transaction.atomic():
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation_id
            )
        )
        lease = HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).first()
        if (
            runtime_is_live(lease)
            or runtime_start_pending(lease)
            or _unacked_commands(conversation).exists()
            or conversation.state not in _ACTIVE_CONVERSATION_STATES
        ):
            return 0
        settle_interrupted_turn(conversation)
        conversation.state = HostedHarnessConversation.State.COLD
        conversation.save(update_fields=["state", "active_invocation_id", "updated_at"])
        return 1


def retire_without_saved_workspace(conversation_id) -> int:
    """Fail waiting messages for good: the run's saved files are gone, so no restart can work."""
    with transaction.atomic():
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation_id
            )
        )
        return _fail_outstanding(
            conversation,
            state=HostedHarnessConversation.State.RETIRED,
            reply=(
                "This environment's saved files are no longer available, so chat "
                "can't start. Rebuild the environment to chat with it again."
            ),
        )


def _abandon_outstanding(conversation_id) -> int:
    with transaction.atomic():
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation_id
            )
        )
        lease = HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).first()
        oldest = _unacked_commands(conversation).order_by("sequence").first()
        if (
            runtime_is_live(lease)
            or runtime_start_pending(lease)
            or oldest is None
            or timezone.now() - oldest.created_at
            <= timedelta(seconds=RUNTIME_GIVE_UP_SECONDS)
        ):
            return 0
        return _fail_outstanding(
            conversation,
            state=HostedHarnessConversation.State.DEGRADED,
            reply=(
                "The environment chat could not start, so your message was not "
                "delivered. Send it again to retry."
            ),
        )


def _fail_outstanding(
    conversation: HostedHarnessConversation, *, state: str, reply: str
) -> int:
    HostedHarnessConversationLease.no_workspace_objects.filter(
        conversation=conversation
    ).update(state=HostedHarnessConversationLease.State.EXPIRED)
    abandoned = _unacked_commands(conversation).update(
        state=HostedHarnessConversationMessage.State.FAILED,
        updated_at=timezone.now(),
    )
    settle_interrupted_turn(conversation)
    conversation.state = state
    if abandoned:
        HostedHarnessConversationMessage.no_workspace_objects.create(
            conversation=conversation,
            sequence=conversation.next_message_sequence,
            role=HostedHarnessConversationMessage.Role.ASSISTANT,
            kind=HostedHarnessConversationMessage.Kind.MESSAGE,
            state=HostedHarnessConversationMessage.State.FAILED,
            stage=conversation.current_stage,
            content=reply,
        )
        conversation.next_message_sequence += 1
    conversation.save(
        update_fields=[
            "state",
            "next_message_sequence",
            "active_invocation_id",
            "updated_at",
        ]
    )
    return abandoned


def record_runtime_started(conversation: HostedHarnessConversation) -> None:
    """A fresh process shows as working until it has answered what is waiting for it."""
    conversation.state = (
        HostedHarnessConversation.State.STARTING
        if _unacked_commands(conversation).exists()
        else HostedHarnessConversation.State.WARM_IDLE
    )
    conversation.save(update_fields=["state", "updated_at"])


def ensure_conversation(job: HostedHarnessJob) -> HostedHarnessConversation:
    conversation, _created = (
        HostedHarnessConversation.no_workspace_objects.get_or_create(
            job=job,
            defaults={
                "organization": job.organization,
                "workspace": job.workspace,
                "current_stage": _logical_stage(job.current_stage),
            },
        )
    )
    return conversation


def enqueue_message(
    job: HostedHarnessJob,
    *,
    content: str,
    client_request_id: str,
    kind: str = "user_message",
    reply_to: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[HostedHarnessConversation, HostedHarnessConversationMessage, bool]:
    with transaction.atomic():
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update()
            .filter(job=job)
            .first()
        )
        if conversation is None:
            conversation = HostedHarnessConversation.no_workspace_objects.create(
                job=job,
                organization=job.organization,
                workspace=job.workspace,
                current_stage=_logical_stage(job.current_stage),
            )
        command_payload = {"command_kind": kind, **(payload or {})}
        existing = conversation.messages.filter(
            client_request_id=client_request_id
        ).first()
        if existing is not None:
            if (
                existing.content != content
                or existing.reply_to != reply_to
                or existing.payload != command_payload
            ):
                raise HostedHarnessError(
                    "conversation_idempotency_conflict",
                    "client_request_id was already used for a different message",
                    status_code=409,
                )
            return conversation, existing, False
        if reply_to is not None:
            target = conversation.messages.filter(
                id=reply_to,
                role=HostedHarnessConversationMessage.Role.ASSISTANT,
                kind__in=(
                    HostedHarnessConversationMessage.Kind.QUESTION,
                    HostedHarnessConversationMessage.Kind.CONFIRMATION,
                ),
            ).first()
            blocking_id = str(
                (conversation.blocking_input or {}).get("message_id") or ""
            )
            already_answered = conversation.messages.filter(reply_to=reply_to).exists()
            if target is None or str(reply_to) != blocking_id or already_answered:
                raise HostedHarnessError(
                    "conversation_reply_target_invalid",
                    "reply_to does not identify the open harness question",
                    status_code=409,
                )
        message = HostedHarnessConversationMessage.no_workspace_objects.create(
            conversation=conversation,
            client_request_id=client_request_id,
            sequence=conversation.next_message_sequence,
            role=HostedHarnessConversationMessage.Role.USER,
            kind=HostedHarnessConversationMessage.Kind.MESSAGE,
            state=HostedHarnessConversationMessage.State.QUEUED,
            stage=conversation.current_stage,
            content=content,
            payload=command_payload,
            reply_to=reply_to,
        )
        conversation.next_message_sequence += 1
        conversation.last_activity_at = timezone.now()
        if conversation.state in {
            HostedHarnessConversation.State.COLD,
            HostedHarnessConversation.State.DEGRADED,
        }:
            conversation.state = HostedHarnessConversation.State.STARTING
        conversation.save(
            update_fields=[
                "next_message_sequence",
                "last_activity_at",
                "state",
                "updated_at",
            ]
        )
        return conversation, message, True


def issue_conversation_capability(
    conversation: HostedHarnessConversation,
    *,
    endpoint_base_url: str,
    provider_ref: str,
    attempt: HostedHarnessAttempt | None,
    ttl_seconds: int,
    control_only: bool = False,
    runtime_name: str = "",
    runtime_digest: str = "",
) -> ConversationCapability:
    token = secrets.token_urlsafe(32)
    fence = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
    lease, _ = HostedHarnessConversationLease.no_workspace_objects.update_or_create(
        conversation=conversation,
        defaults={
            "attempt": attempt,
            "control_only": control_only,
            "provider_ref": provider_ref,
            "state": HostedHarnessConversationLease.State.STARTING,
            "token_hash": hash_secret(token),
            "fence_hash": hash_secret(fence),
            "expires_at": expires_at,
            "heartbeat_at": timezone.now(),
            "snapshot_name": runtime_name
            or str(getattr(settings, "ALK_DAYTONA_SNAPSHOT", "") or ""),
            "snapshot_digest": runtime_digest
            or str(getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", "") or ""),
        },
    )
    base = endpoint_base_url.rstrip("/")
    prefix = f"{base}/simulate/api/harness/conversations/{conversation.id}"
    document = {
        "schema_version": CONVERSATION_SCHEMA_VERSION,
        "conversation_id": str(conversation.id),
        "job_id": str(conversation.job_id),
        "token": token,
        "fence": fence,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "identity": {
            "app_name": "alk-harness",
            "user_id": f"conversation-{conversation.id}",
        },
        "turn_context": build_turn_context(conversation),
        "endpoints": {
            "commands": f"{prefix}/commands/",
            "events": f"{prefix}/events/",
            "rerun": f"{prefix}/rerun/",
            "adjust": f"{prefix}/adjust/",
            "session_store": f"{prefix}/session-store/",
            "session_store_append": f"{prefix}/session-store/append/",
            "run_status": f"{prefix}/run-status/",
            "workspace": f"{prefix}/workspace/",
        },
    }
    return ConversationCapability(
        lease=lease, token=token, fence=fence, document=document
    )


def build_turn_context(conversation: HostedHarnessConversation) -> dict[str, Any]:
    job = conversation.job
    lease = (
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation,
            state__in=(
                HostedHarnessConversationLease.State.STARTING,
                HostedHarnessConversationLease.State.ACTIVE,
            ),
        )
        .only("control_only")
        .first()
    )
    return {
        "schema_version": "futureagi.harness-turn-context.v1",
        "conversation_id": str(conversation.id),
        "stage": conversation.current_stage,
        "active_invocation_id": conversation.active_invocation_id,
        "environment": {
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "workspace_revision": conversation.latest_workspace_digest,
            "source_revision": (job.payload.get("source") or {}).get("commit_sha"),
        },
        "selection": {
            "run_test_id": str(job.run_test_id) if job.run_test_id else None,
            "test_execution_id": str(job.test_execution_id)
            if job.test_execution_id
            else None,
            "scenario_ids": [
                str(value)
                for value in job.scenario_registrations.order_by(
                    "created_at"
                ).values_list("scenario_id", flat=True)[:200]
            ],
        },
        "status": {
            "state": job.state,
            "stage": job.current_stage,
            "completed_scenarios": job.completed_count,
            "failed_scenarios": job.failed_count,
            "total_scenarios": job.scenario_count,
        },
        "operation": None,
        "blocking_gap": conversation.blocking_input,
        "capabilities": {
            "policy_hash": conversation.policy_hash,
            "control_only": bool(lease and lease.control_only),
        },
        "event_watermark": conversation.event_acked_through,
        "command_watermark": conversation.command_acked_through,
    }


def load_provider_transcript(
    conversation: HostedHarnessConversation,
    *,
    project_key: str,
    provider_session_id: str,
    subpath: str,
) -> dict[str, Any]:
    transcript = (
        HostedHarnessConversationTranscript.no_workspace_objects.filter(
            conversation=conversation,
            project_key=project_key,
            provider_session_id=provider_session_id,
            subpath=subpath,
        )
        .only("entries")
        .first()
    )
    subkeys = list(
        HostedHarnessConversationTranscript.no_workspace_objects.filter(
            conversation=conversation,
            project_key=project_key,
            provider_session_id=provider_session_id,
        )
        .exclude(subpath="")
        .order_by("subpath")
        .values_list("subpath", flat=True)
    )
    return {
        "entries": copy.deepcopy(transcript.entries) if transcript else None,
        "subkeys": subkeys,
    }


def append_provider_transcript(
    conversation: HostedHarnessConversation,
    *,
    project_key: str,
    provider_session_id: str,
    subpath: str,
    entries: list[dict[str, Any]],
) -> dict[str, int]:
    with transaction.atomic():
        HostedHarnessConversation.no_workspace_objects.select_for_update().only(
            "id"
        ).get(id=conversation.id)
        transcript, _ = (
            HostedHarnessConversationTranscript.no_workspace_objects.get_or_create(
                conversation=conversation,
                project_key=project_key,
                provider_session_id=provider_session_id,
                subpath=subpath,
                defaults={"entries": []},
            )
        )
        transcript = HostedHarnessConversationTranscript.no_workspace_objects.select_for_update().get(
            id=transcript.id
        )
        stored = list(transcript.entries or [])
        seen = {
            str(entry["uuid"])
            for entry in stored
            if isinstance(entry, dict) and entry.get("uuid")
        }
        appended = 0
        for entry in entries:
            entry_uuid = str(entry.get("uuid") or "")
            if entry_uuid and entry_uuid in seen:
                continue
            stored.append(copy.deepcopy(entry))
            appended += 1
            if entry_uuid:
                seen.add(entry_uuid)
        encoded_size = len(
            json.dumps(
                stored,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        )
        if (
            len(stored) > TRANSCRIPT_ENTRY_LIMIT
            or encoded_size > TRANSCRIPT_BYTES_LIMIT
        ):
            raise HostedHarnessError(
                "conversation_transcript_too_large",
                "The hosted model transcript exceeded its bounded retention window",
                status_code=413,
            )
        if appended:
            transcript.entries = stored
            transcript.save(update_fields=["entries", "updated_at"])
    return {"appended": appended}


def pending_commands(
    conversation: HostedHarnessConversation, *, after: int
) -> dict[str, Any]:
    now = timezone.now()
    lease = getattr(conversation, "lease", None)
    if lease is not None and (
        lease.state == HostedHarnessConversationLease.State.STARTING
        or lease.heartbeat_at is None
        or now - lease.heartbeat_at >= timedelta(seconds=_HEARTBEAT_WRITE_SECONDS)
    ):
        HostedHarnessConversationLease.no_workspace_objects.filter(
            id=lease.id,
            token_hash=lease.token_hash,
            state__in=[
                HostedHarnessConversationLease.State.STARTING,
                HostedHarnessConversationLease.State.ACTIVE,
            ],
        ).update(heartbeat_at=now, state=HostedHarnessConversationLease.State.ACTIVE)
    messages = list(
        _unacked_commands(conversation)
        .filter(
            sequence__gt=after,
        )
        .order_by("sequence")[:COMMAND_LIMIT]
    )
    return {
        "schema_version": CONVERSATION_SCHEMA_VERSION,
        "commands": [
            {
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "sequence": message.sequence,
                "kind": message.payload.get("command_kind", "user_message"),
                "expected_environment_revision": conversation.latest_workspace_digest,
                "payload": {
                    "content": message.content,
                    "reply_to": str(message.reply_to) if message.reply_to else None,
                    **{
                        key: value
                        for key, value in message.payload.items()
                        if key != "command_kind"
                    },
                },
            }
            for message in messages
        ],
        "turn_context": build_turn_context(conversation),
    }


def ingest_conversation_events(
    conversation: HostedHarnessConversation,
    *,
    acknowledged_through: int,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    with transaction.atomic():
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update().get(
                id=conversation.id
            )
        )
        lease = (
            HostedHarnessConversationLease.no_workspace_objects.select_for_update().get(
                conversation=conversation
            )
        )
        latest_command = (
            conversation.messages.filter(
                role=HostedHarnessConversationMessage.Role.USER
            )
            .order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
            or 0
        )
        if acknowledged_through > latest_command:
            raise HostedHarnessError(
                "conversation_ack_out_of_range",
                f"latest conversation command is {latest_command}",
                status_code=422,
            )
        if acknowledged_through > conversation.command_acked_through:
            conversation.command_acked_through = acknowledged_through
            conversation.messages.filter(
                role=HostedHarnessConversationMessage.Role.USER,
                sequence__lte=acknowledged_through,
                state=HostedHarnessConversationMessage.State.QUEUED,
            ).update(state=HostedHarnessConversationMessage.State.DELIVERED)
        for event in events:
            _ingest_event(conversation, event)
        lease.command_watermark = max(
            lease.command_watermark, conversation.command_acked_through
        )
        lease.event_watermark = conversation.event_acked_through
        lease.heartbeat_at = timezone.now()
        lease.state = HostedHarnessConversationLease.State.ACTIVE
        lease.save(
            update_fields=[
                "command_watermark",
                "event_watermark",
                "heartbeat_at",
                "state",
                "updated_at",
            ]
        )
        conversation.last_activity_at = timezone.now()
        conversation.save(
            update_fields=[
                "command_acked_through",
                "event_acked_through",
                "next_event_sequence",
                "next_message_sequence",
                "state",
                "current_stage",
                "active_invocation_id",
                "blocking_input",
                "last_activity_at",
                "updated_at",
            ]
        )
    return {"acked_through_sequence": conversation.event_acked_through}


def _ingest_event(
    conversation: HostedHarnessConversation, event: dict[str, Any]
) -> None:
    canonical = {key: value for key, value in event.items() if key != "digest"}
    if canonical_digest(canonical) != event["digest"]:
        raise HostedHarnessError(
            "conversation_event_digest_mismatch",
            "conversation event digest did not match its body",
            status_code=422,
        )
    existing = HostedHarnessConversationEvent.no_workspace_objects.filter(
        event_id=event["event_id"]
    ).first()
    if existing is not None:
        if (
            existing.digest != event["digest"]
            or existing.conversation_id != conversation.id
        ):
            raise HostedHarnessError(
                "conversation_event_conflict",
                "event_id is already bound to different content",
                status_code=409,
            )
        return
    expected = conversation.event_acked_through + 1
    if event["sequence"] != expected:
        raise HostedHarnessError(
            "conversation_event_out_of_order",
            f"expected conversation event sequence {expected}",
            status_code=409,
            retryable=True,
        )
    HostedHarnessConversationEvent.no_workspace_objects.create(
        event_id=event["event_id"],
        conversation=conversation,
        sequence=event["sequence"],
        kind=event["kind"],
        message_id=event.get("message_id"),
        stage=event.get("stage") or "",
        invocation_id=event.get("invocation_id") or None,
        function_call_id=event.get("function_call_id") or None,
        payload=event.get("payload") or {},
        digest=event["digest"],
        emitted_at=event["emitted_at"],
    )
    conversation.event_acked_through = event["sequence"]
    conversation.next_event_sequence = event["sequence"] + 1
    _project_event(conversation, event)


def _project_event(
    conversation: HostedHarnessConversation, event: dict[str, Any]
) -> None:
    kind = event["kind"]
    payload = event.get("payload") or {}
    message_id = event.get("message_id")
    if event.get("stage"):
        conversation.current_stage = event["stage"]
    if event.get("invocation_id"):
        conversation.active_invocation_id = event["invocation_id"]
    if kind == "turn_started":
        conversation.state = HostedHarnessConversation.State.RESPONDING
        command_id = payload.get("command_message_id")
        if command_id:
            conversation.messages.filter(
                id=command_id,
                role=HostedHarnessConversationMessage.Role.USER,
            ).update(state=HostedHarnessConversationMessage.State.DELIVERED)
        return
    if kind == "stage_changed":
        conversation.current_stage = str(
            payload.get("to") or conversation.current_stage
        )
        return
    if kind in {"assistant_delta", "assistant_message"}:
        if not message_id:
            raise HostedHarnessError(
                "conversation_message_id_missing",
                f"{kind} requires message_id",
                status_code=422,
            )
        message = conversation.messages.filter(id=message_id).first()
        if message is None:
            message = HostedHarnessConversationMessage.no_workspace_objects.create(
                id=message_id,
                conversation=conversation,
                sequence=conversation.next_message_sequence,
                role=HostedHarnessConversationMessage.Role.ASSISTANT,
                kind=HostedHarnessConversationMessage.Kind.MESSAGE,
                state=HostedHarnessConversationMessage.State.STREAMING,
                stage=event.get("stage") or conversation.current_stage,
                content="",
                invocation_id=event.get("invocation_id") or None,
            )
            conversation.next_message_sequence += 1
        text = str(payload.get("text") or "")
        if kind == "assistant_delta":
            message.content += text
            message.state = HostedHarnessConversationMessage.State.STREAMING
        else:
            message.content = text or message.content
            message.state = HostedHarnessConversationMessage.State.COMPLETED
        message.save(update_fields=["content", "state", "updated_at"])
        return
    if kind in {"question_requested", "confirmation_requested"}:
        if not message_id:
            raise HostedHarnessError(
                "conversation_message_id_missing",
                f"{kind} requires message_id",
                status_code=422,
            )
        HostedHarnessConversationMessage.no_workspace_objects.get_or_create(
            id=message_id,
            defaults={
                "conversation": conversation,
                "sequence": conversation.next_message_sequence,
                "role": HostedHarnessConversationMessage.Role.ASSISTANT,
                "kind": (
                    HostedHarnessConversationMessage.Kind.QUESTION
                    if kind == "question_requested"
                    else HostedHarnessConversationMessage.Kind.CONFIRMATION
                ),
                "state": HostedHarnessConversationMessage.State.COMPLETED,
                "stage": event.get("stage") or conversation.current_stage,
                "content": str(payload.get("prompt") or ""),
                "payload": payload,
                "invocation_id": event.get("invocation_id") or None,
                "function_call_id": event.get("function_call_id") or None,
            },
        )
        conversation.next_message_sequence += 1
        conversation.state = HostedHarnessConversation.State.WAITING_FOR_USER
        conversation.blocking_input = {
            "message_id": str(message_id),
            "kind": kind,
            **payload,
        }
        return
    if kind == "turn_interrupted":
        control_id = payload.get("interrupt_message_id") or payload.get(
            "command_message_id"
        )
        if control_id:
            conversation.messages.filter(
                id=control_id,
                role=HostedHarnessConversationMessage.Role.USER,
            ).update(state=HostedHarnessConversationMessage.State.COMPLETED)
        conversation.state = HostedHarnessConversation.State.WARM_IDLE
        conversation.active_invocation_id = None
        return
    if kind == "turn_completed":
        command_id = payload.get("command_message_id")
        if command_id:
            conversation.messages.filter(
                id=command_id,
                role=HostedHarnessConversationMessage.Role.USER,
            ).update(state=HostedHarnessConversationMessage.State.COMPLETED)
        blocking_message_id = (conversation.blocking_input or {}).get("message_id")
        answered = bool(
            blocking_message_id
            and conversation.messages.filter(
                role=HostedHarnessConversationMessage.Role.USER,
                reply_to=blocking_message_id,
                sequence__lte=conversation.command_acked_through,
            ).exists()
        )
        if answered:
            conversation.blocking_input = None
        if (
            conversation.state != HostedHarnessConversation.State.WAITING_FOR_USER
            or answered
        ):
            conversation.state = HostedHarnessConversation.State.WARM_IDLE
        conversation.active_invocation_id = None


def conversation_run_status(
    conversation: HostedHarnessConversation,
) -> dict[str, Any]:
    job = HostedHarnessJob.no_workspace_objects.get(id=conversation.job_id)
    receipts = list(
        HostedHarnessReceipt.no_workspace_objects.filter(job=job)
        .order_by("created_at")
        .values_list("body", flat=True)[: job.scenario_count]
    )
    return {
        "job_id": str(job.id),
        "state": job.state,
        "stage": job.current_stage,
        "completed_scenarios": job.completed_count,
        "failed_scenarios": job.failed_count,
        "total_scenarios": job.scenario_count,
        "receipts": receipts,
    }


def prepare_conversation_rerun(
    conversation: HostedHarnessConversation,
) -> HostedHarnessJob:
    """Promote the latest chat checkpoint to the job's next immutable run input."""
    terminal_states = {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
    with transaction.atomic():
        conversation = (
            HostedHarnessConversation.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=conversation.id)
        )
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=conversation.job_id
        )
        if job.state not in terminal_states:
            raise HostedHarnessError(
                "job_not_terminal",
                f"hosted harness job cannot be rerun while it is {job.state}",
                status_code=409,
            )
        if not conversation.latest_workspace_object_key:
            raise HostedHarnessError(
                "conversation_workspace_not_ready",
                "The conversation has not committed an environment checkpoint",
                status_code=409,
                retryable=True,
            )
        if (
            conversation.latest_scenario_count is not None
            and not 1 <= conversation.latest_scenario_count <= 200
        ):
            raise HostedHarnessError(
                "conversation_scenario_count_invalid",
                "A hosted run requires between 1 and 200 saved scenarios",
                status_code=422,
            )
        payload = copy.deepcopy(job.payload)
        metadata = payload.setdefault("metadata", {})
        metadata["authoring_object_key"] = conversation.latest_workspace_object_key
        metadata["authoring_digest"] = conversation.latest_workspace_digest
        metadata.pop("scenario_extend", None)
        payload["metadata"] = metadata
        job.payload = payload
        if conversation.latest_scenario_count is not None:
            job.scenario_count = conversation.latest_scenario_count
        job.save(update_fields=["payload", "scenario_count", "updated_at"])
    return job


def store_workspace_archive(
    conversation: HostedHarnessConversation,
    *,
    digest: str,
    size: int,
    body: bytes,
) -> dict[str, Any]:
    if size != len(body) or size > WORKSPACE_MAX_BYTES:
        raise HostedHarnessError(
            "workspace_size_invalid",
            "workspace archive size is invalid or exceeds 256 MiB",
            status_code=413,
        )
    actual = "sha256:" + hashlib.sha256(body).hexdigest()
    if digest != actual:
        raise HostedHarnessError(
            "workspace_digest_mismatch",
            "workspace archive digest did not match",
            status_code=422,
        )
    _validate_workspace_archive(body)
    scenario_count = _workspace_scenario_count(body)
    key = (
        f"harness-conversations/{conversation.organization_id}/"
        f"{conversation.id}/{actual[7:]}.tar.gz"
    )
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        UPLOAD_BUCKET_NAME,
        key,
        io.BytesIO(body),
        len(body),
        content_type="application/gzip",
    )
    HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id).update(
        latest_workspace_digest=actual,
        latest_workspace_object_key=key,
        latest_scenario_count=scenario_count,
        last_activity_at=timezone.now(),
    )
    return {"digest": actual, "size": size}


def load_workspace_archive(conversation: HostedHarnessConversation) -> bytes | None:
    if not conversation.latest_workspace_object_key:
        return None
    try:
        response = get_storage_client().get_object(
            UPLOAD_BUCKET_NAME, conversation.latest_workspace_object_key
        )
    except S3Error as exc:
        if exc.code != "NoSuchKey":
            raise
        # The checkpoint object is gone; treat it like no checkpoint so chat falls back
        # to the authoring archive rather than retiring. The user's edits are lost.
        logger.warning(
            "conversation checkpoint object missing; falling back to authoring archive conversation=%s",
            conversation.id,
        )
        return None
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _validate_workspace_archive(body: bytes) -> None:
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive.getmembers():
                path = member.name.replace("\\", "/")
                if (
                    not path
                    or path.startswith("/")
                    or ".." in path.split("/")
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise ValueError(path)
    except (tarfile.TarError, ValueError) as exc:
        raise HostedHarnessError(
            "workspace_archive_invalid",
            "workspace archive contains an invalid or unsafe member",
            status_code=422,
        ) from exc


def _workspace_scenario_count(body: bytes) -> int:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        return len(
            {
                parts[1]
                for member in archive.getmembers()
                if len(parts := member.name.replace("\\", "/").split("/")) == 3
                and parts[0] == "scenarios"
                and parts[2] == "scenario.json"
            }
        )


def _serialize_message(message: HostedHarnessConversationMessage) -> dict[str, Any]:
    return {
        "message_id": str(message.id),
        "sequence": message.sequence,
        "role": message.role,
        "kind": message.kind,
        "state": message.state,
        "stage": message.stage,
        "content": message.content,
        "payload": message.payload,
        "invocation_id": message.invocation_id,
        "function_call_id": message.function_call_id,
        "reply_to": str(message.reply_to) if message.reply_to else None,
        "created_at": message.created_at.isoformat(),
    }


def _serialize_event(event: HostedHarnessConversationEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "kind": event.kind,
        "message_id": str(event.message_id) if event.message_id else None,
        "stage": event.stage,
        "invocation_id": event.invocation_id,
        "function_call_id": event.function_call_id,
        "payload": event.payload,
        "emitted_at": event.emitted_at.isoformat(),
    }


def _logical_stage(stage: str) -> str:
    if stage in {"understanding_agent"}:
        return "understand"
    if stage in {
        "generating_environment",
        "building_environment",
        "validating_environment",
    }:
        return "build"
    if stage in {"generating_scenarios", "validating_scenarios"}:
        return "scenarios"
    if stage in {
        "connecting_agent",
        "running",
        "grading",
        "uploading_artifacts",
        "completed",
        "failed",
        "canceled",
    }:
        return "run"
    return "reception"
