from __future__ import annotations

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity


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
        return str(lease.id)
    finally:
        close_old_connections()
