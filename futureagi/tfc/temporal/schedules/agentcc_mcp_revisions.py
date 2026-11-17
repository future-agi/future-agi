"""MCP tool-revision schedules for the agentcc gateway (M21).

``agentcc-mcp-revisions-pull`` (every minute) brings each org's held tool
revisions up to date with the gateway (``GET /-/mcp/revisions``) and pins the
tools of a server that has no pins yet as the gateway first listed them
(``agentcc.services.mcp_revisions.pull_all``).
"""

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)

# A run that overlaps the next tick is skipped (ScheduleOverlapPolicy.SKIP).
PULL_TIME_LIMIT = 120


@temporal_activity(time_limit=PULL_TIME_LIMIT, queue="default", max_retries=0)
def pull_agentcc_mcp_revisions_activity():
    """One tick: every org with MCP servers, one gateway pull each."""
    from agentcc.services.mcp_revisions import pull_all

    result = pull_all()
    logger.info("agentcc_mcp_revisions_pulled", **result)
    return result


AGENTCC_MCP_REVISION_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="agentcc-mcp-revisions-pull",
        activity_name="pull_agentcc_mcp_revisions_activity",
        interval_seconds=60,
        queue="default",
        description=(
            "Pull the MCP tools the gateway holds for review and pin the tools "
            "of newly added MCP servers (every minute)"
        ),
    ),
]
