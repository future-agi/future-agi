"""
Tracer-app database routing constants — feature-key gated, computed once.

Mirrors PostHog's pattern from `posthog/models/feature_flag/local_evaluation.py`
where they build constants like `DATABASE_FOR_LOCAL_EVALUATION` that switch
based on `settings.READ_REPLICA_OPT_IN`. We use namespaced `feature:` keys
to avoid collisions with model class names (see `tfc/routers.py`).

How to use these constants:

    from tracer.db_routing import DATABASE_FOR_DASHBOARD_LIST

    queryset = self.get_queryset().using(DATABASE_FOR_DASHBOARD_LIST)
    # or, when constructing from scratch:
    rows = Dashboard.objects.db_manager(DATABASE_FOR_DASHBOARD_LIST).filter(...)

When `READ_REPLICA_OPT_IN` is empty (default), all constants resolve to
"default" — i.e. the routing here is a no-op until explicitly enabled. To
flip a single endpoint to replica:

    READ_REPLICA_OPT_IN=feature:dashboard_list

CAVEATS:

  - These constants are computed at import time. Env-var changes require
    a worker restart to take effect here.
  - Use only on **list / browse** paths that can tolerate ~100ms staleness.
    Do NOT use on read-after-write paths (post-save GET, in-process reads
    after the user just edited).
  - Do NOT chain `.select_for_update()` after `db_manager(...)` / `.using(...)`
    that resolves to "replica" — the router can't catch this, and a
    locking read on a standby fails at the PG level. See
    `internal-docs/design/do-and-do-not.md`.
"""

from django.conf import settings


def _replica_if_opted_in(feature_key: str) -> str:
    """Return "replica" iff the feature key is opted in AND a replica is configured."""
    opt_in = getattr(settings, "READ_REPLICA_OPT_IN", []) or []
    if feature_key not in opt_in:
        return "default"
    if "replica" not in settings.DATABASES:
        return "default"
    return "replica"


# Dashboard list endpoint — browses all dashboards in a workspace.
# High-traffic. Staleness is presentational (a dashboard saved <100ms ago
# might not appear in the list until the next refresh).
DATABASE_FOR_DASHBOARD_LIST = _replica_if_opted_in("feature:dashboard_list")

# SavedView list endpoint — browses saved trace/span/voice filter views.
# Same profile as above.
DATABASE_FOR_SAVED_VIEW_LIST = _replica_if_opted_in("feature:saved_view_list")

# ReplaySession list endpoint — paginated browse, project-scoped.
# Serializer reads only `project.name`; queryset uses `select_related("project")`.
# Staleness is acceptable: a session created mid-page is fine to show next refresh.
DATABASE_FOR_REPLAY_SESSION_LIST = _replica_if_opted_in("feature:replay_session_list")

# CustomEvalConfig list endpoint — filter by org / project / task_id.
# Pure routing: main SELECT goes to replica. Pre-existing N+1 in the
# serializer (`get_eval_group` reads `obj.eval_group.name` per row)
# remains — those per-row FK fetches go through the router and likely
# land on `default` since EvalGroup isn't opted in. Not fixing here.
DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST = _replica_if_opted_in(
    "feature:custom_eval_config_list"
)

# ProjectVersion ids dropdown — paginates a serialized list in memory.
# Note: there's a separate perf bug here (loads all versions, paginates
# after serialize). Routing it off primary at least shifts that load to
# the replica until that's fixed.
DATABASE_FOR_PROJECT_VERSION_IDS = _replica_if_opted_in("feature:project_version_ids")

# Project list endpoint (tracer/views/project.py::list_projects).
#
# Identified via Sentry (7-day window): 28,051 calls/week, ~1012ms p95 on
# the PG SELECT, ~4,032 seconds of total PG time per week — the single
# highest-impact unrouted endpoint at the time of measurement. Pure-PG
# list with .only(...) field restriction; ClickHouse handles the volume
# enrichment downstream. The accompanying ProjectVersion count aggregate
# in the same view should use the same alias so both queries land
# together; we re-use this constant for it.
DATABASE_FOR_PROJECT_LIST = _replica_if_opted_in("feature:project_list")
