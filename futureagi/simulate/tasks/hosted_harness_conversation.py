from __future__ import annotations

import structlog
from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(
    time_limit=1200,
    max_retries=3,
    queue="simulation_runner",
)
def ensure_hosted_harness_conversation_runtime(
    conversation_id: str,
    endpoint_base_url: str,
) -> str:
    """Start or reuse the replaceable managed runtime for one durable conversation."""
    from simulate.models import HostedHarnessConversation
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    close_old_connections()
    try:
        conversation = HostedHarnessConversation.no_workspace_objects.select_related(
            "job",
            "job__organization",
        ).get(id=conversation_id)
        lease = HostedHarnessGateway().ensure_conversation_runtime(
            conversation.job,
            conversation=conversation,
            endpoint_base_url=endpoint_base_url,
        )
        return str(lease.id) if lease is not None else ""
    finally:
        close_old_connections()


def schedule_conversation_runtime(
    conversation_id: str, endpoint_base_url: str, *, wakeup: str | None = None
):
    """Ask for a runtime; requests for one conversation coalesce while a start is running.

    A ``wakeup`` is a request that must not be absorbed by a start that is running: that
    start may have read the state before what the wakeup is about happened. It has an id
    of its own, and repeats of the same wakeup coalesce."""
    from temporalio.common import WorkflowIDConflictPolicy

    task_id = f"hosted-conversation-runtime-{conversation_id}"
    return ensure_hosted_harness_conversation_runtime.apply_async(
        args=[conversation_id, endpoint_base_url],
        task_id=f"{task_id}-{wakeup}" if wakeup else task_id,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


@temporal_activity(time_limit=600, max_retries=3, queue="default")
def promote_hosted_harness_conversation_checkpoint(
    conversation_id: str, turn_sequence: int
) -> str:
    """Publish a finished chat turn's workspace to its environment."""
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_conversation import (
        promote_turn_checkpoint,
        record_platform_landing_refused,
        report_checkpoint_rejection,
        start_chat_after_platform_landing,
    )

    try:
        environment = promote_turn_checkpoint(conversation_id, turn_sequence)
    except HostedHarnessError as exc:
        if exc.retryable:
            raise
        # A checkpoint that cannot be published stays unpublished; retrying cannot fix it.
        logger.error(
            "hosted_conversation_checkpoint_rejected",
            conversation_id=conversation_id,
            code=exc.code,
            message=exc.message,
        )
        if exc.code in {
            "scenario_key_retired",
            "scenario_key_taken",
            "draft_changed",
            "conversation_checkpoint_stale",
        }:
            # The person has to act (rename, or ask again), so they need to see why.
            report_checkpoint_rejection(conversation_id, exc.message)
        record_platform_landing_refused(
            conversation_id=conversation_id, turn_sequence=turn_sequence
        )
        start_chat_after_platform_landing(
            conversation_id=conversation_id, turn_sequence=turn_sequence
        )
        return ""
    start_chat_after_platform_landing(
        conversation_id=conversation_id, turn_sequence=turn_sequence
    )
    return str(environment.id) if environment is not None else ""


@temporal_activity(time_limit=120, max_retries=0, queue="default")
def recover_hosted_harness_conversations() -> dict[str, int]:
    """Restart chat runtimes that died holding messages; the next tick retries a failure."""
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_conversation import (
        recover_stalled_conversations,
    )
    from simulate.services.hosted_harness_ingress import _public_base_url

    try:
        base_url = _public_base_url(None)
    except HostedHarnessError:
        # Every hosted callback needs this URL, so a stack without it cannot run chat at all.
        logger.error("hosted_conversation_recovery_disabled", reason="no_public_url")
        return {}
    close_old_connections()
    try:
        counts = recover_stalled_conversations(
            lambda conversation_id: schedule_conversation_runtime(
                conversation_id, base_url
            )
        )
    finally:
        close_old_connections()
    try:
        from simulate.services.harness_versioning.rebuild import (
            resend_unsent_sandbox_stops,
        )

        resent = resend_unsent_sandbox_stops()
        if resent:
            counts["stops_resent"] = resent
    except Exception:  # noqa: BLE001 - the next tick sends them
        logger.exception("hosted_conversation_stop_resend_failed")
    finally:
        close_old_connections()
    if counts.get("restarted") or counts.get("settled") or counts.get("abandoned"):
        logger.info("hosted_conversation_recovery", **counts)
    return counts
