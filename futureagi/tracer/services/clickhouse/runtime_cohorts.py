"""Bounded server-side trace sets for runtime → Observe drilldowns.

The handle is not authorization: Observe must still authorize its project(s).
An expired/evicted set is an error, never an unfiltered or approximate query.
Deterministic keyed handles deduplicate identical sets across cells/refreshes.
"""

import json
import re

from django.core.cache import cache
from django.utils.crypto import salted_hmac

MAX_COHORT_TRACES = 20000
COHORT_TTL_SECONDS = 24 * 60 * 60
_TOKEN = re.compile(r"^[0-9a-f]{64}$")


def cohort_filter(project_id, trace_ids):
    ids = sorted(set(trace_ids))
    if len(ids) > MAX_COHORT_TRACES:
        raise ValueError("Runtime trace set is too large.")
    value = {"project": str(project_id), "traces": ids}
    token = salted_hmac("runtime-trace-cohort-v1", json.dumps(value, sort_keys=True), algorithm="sha256").hexdigest()
    cache.set(f"runtime-cohort:{token}", value, timeout=COHORT_TTL_SECONDS)
    return {"column_id": "skill.trace_cohort", "filter_config": {
        "col_type": "SKILL", "filter_type": "text", "filter_op": "equals", "filter_value": token,
    }}


def resolve_cohort(token, project_ids):
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("Invalid runtime trace set. Reopen the Runtime card.")
    try:
        value = cache.get(f"runtime-cohort:{token}")
    except Exception as exc:
        raise ValueError("Runtime trace set is unavailable. Try again.") from exc
    if value is None:
        raise ValueError("Runtime trace set expired. Reopen the Runtime card.")
    if value["project"] not in {str(project) for project in project_ids}:
        raise ValueError("Runtime trace set is outside the selected project.")
    return value["project"], tuple(value["traces"])
