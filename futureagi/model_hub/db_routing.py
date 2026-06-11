"""
model_hub-app database routing constants — feature-key gated, computed once.

Mirrors `tracer/db_routing.py`. See that file's docstring for the full
contract. Same caveats apply:

  - Constants are computed at import time. Env-var changes require a
    worker restart.
  - Use only on list / browse paths that can tolerate ~100ms staleness.
  - Do NOT chain `.select_for_update()` after `.using(...)` that resolves
    to "replica" — the router can't catch this and the standby will reject.
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


# Dataset list endpoint — paginated browse of all datasets in an org.
# Annotates derived counts (datapoints, experiments, optimisations,
# derived datasets) via Subquery. Already N+1-free. Staleness is
# acceptable: a dataset created <100ms ago might not appear in the list
# until the next refresh.
DATABASE_FOR_DATASET_LIST = _replica_if_opted_in("feature:dataset_list")

# EvalGroup list endpoint (model_hub/views/eval_group.py::EvalGroupView.list).
#
# Identified via Sentry (7-day window):
#   - 150,600 calls/week
#   - p95 ~4ms on the SQL
#   - ~746s/week of total PG time
#
# Three independent bulk reads (main EvalGroup queryset, through-table for
# template relationships, EvalTemplate). All are pre-existing bulk reads
# with no per-row queries. Pure routing: add .using() to each.
DATABASE_FOR_EVAL_GROUP_LIST = _replica_if_opted_in("feature:eval_group_list")
