from __future__ import annotations

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

__all__ = ["sweep_lost_agent_fixes"]


# No retry: the body settles each fix on its own, and the next pass picks up whatever
# this one could not.
@temporal_activity(time_limit=300, max_retries=0, queue="default")
def sweep_lost_agent_fixes() -> dict[str, int]:
    """End "Fix my agent" runs whose workflow is gone, so none stays active forever."""
    from simulate.services.agent_fix_sweep import sweep_lost_fix_runs

    close_old_connections()
    try:
        return sweep_lost_fix_runs()
    finally:
        close_old_connections()
