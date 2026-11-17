"""MCP credential schedules for the agentcc gateway.

Credential expiry alerts and refreshes register their ``ScheduleConfig``
entries here.

- ``agentcc-mcp-shared-evictions-retry`` (every minute) retries the shared
  static credential evictions the gateway has not confirmed (W4), a bounded
  number per tick, so a revoked, lapsed or replaced shared secret's revision
  never waits for its org's next credential change to stop on every gateway
  replica (``agentcc.services.mcp_shared_credential_lifecycle.retry_pending``).
  With nothing pending a tick reads one query and calls nothing.
"""

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)

# A run that overlaps the next tick is skipped (ScheduleOverlapPolicy.SKIP);
# the tick stops starting slots after its own budget, well inside this.
RETRY_TIME_LIMIT = 120


@temporal_activity(time_limit=RETRY_TIME_LIMIT, queue="default", max_retries=0)
def retry_agentcc_mcp_shared_evictions_activity():
    """One tick: the unconfirmed shared static evictions, bounded."""
    from agentcc.services.mcp_shared_credential_lifecycle import retry_pending

    result = retry_pending()
    logger.info("agentcc_mcp_shared_evictions_retried", **result)
    return result


AGENTCC_MCP_CREDENTIAL_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="agentcc-mcp-shared-evictions-retry",
        activity_name="retry_agentcc_mcp_shared_evictions_activity",
        interval_seconds=60,
        queue="default",
        description=(
            "Retry the shared MCP credential gateway evictions the gateway has "
            "not confirmed, a bounded number per tick (every minute)"
        ),
    ),
]
