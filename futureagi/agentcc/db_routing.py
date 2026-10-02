"""
agentcc-app database routing constants — feature-key gated, computed once.

Mirrors `tracer/db_routing.py` and `model_hub/db_routing.py`. See those for
the full contract. Same caveats apply.
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


# Gateway-startup bulk org config fetch (agentcc/views/org_config_bulk.py).
#
# Identified via Sentry (7-day window):
#   - 661,519 calls/week (highest volume of any PG endpoint)
#   - p95 ~3ms on the SQL (queries already fast)
#   - ~1,553s/week of total PG time — volume × fast query
#
# The endpoint is the gateway sync path: returns all active org configs at
# startup. Configs change rarely so staleness ≤ replication lag is fine.
# Single queryset with select_related("organization") — no N+1, no per-row
# queries.
DATABASE_FOR_ORG_CONFIG_BULK = _replica_if_opted_in("feature:org_config_bulk")
