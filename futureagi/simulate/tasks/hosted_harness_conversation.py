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


def schedule_conversation_runtime(conversation_id: str, endpoint_base_url: str):
    """Ask for a runtime; requests for one conversation coalesce while a start is running."""
    from temporalio.common import WorkflowIDConflictPolicy

    return ensure_hosted_harness_conversation_runtime.apply_async(
        args=[conversation_id, endpoint_base_url],
        task_id=f"hosted-conversation-runtime-{conversation_id}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


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
    if counts.get("restarted") or counts.get("settled") or counts.get("abandoned"):
        logger.info("hosted_conversation_recovery", **counts)
    return counts
