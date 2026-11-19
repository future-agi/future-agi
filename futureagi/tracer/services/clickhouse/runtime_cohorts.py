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


def cohort_filter(project_id, trace_ids, *, latency_window=None):
    ids = sorted(set(trace_ids))
    if len(ids) > MAX_COHORT_TRACES:
        raise ValueError("Runtime trace set is too large.")
    value = {"project": str(project_id), "traces": ids}
    if latency_window is not None:
        value["latency_window"] = list(latency_window)
    token = salted_hmac(
        "runtime-trace-cohort-v1", json.dumps(value, sort_keys=True), algorithm="sha256"
    ).hexdigest()
    cache.set(f"runtime-cohort:{token}", value, timeout=COHORT_TTL_SECONDS)
    return {
        "column_id": "skill.trace_cohort",
        "filter_config": {
            "col_type": "SKILL",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": token,
        },
    }


def resolve_cohort(token, project_ids, *, latency_window=None):
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
    if latency_window is not None and value.get("latency_window") != list(
        latency_window
    ):
        raise ValueError("Runtime latency window changed. Reopen the Runtime card.")
    return value["project"], tuple(value["traces"])


LATENCY_DESC = [{"column_id": "latency", "direction": "desc"}]


def latency_cohort_token(sort_params, filters):
    """The only supported order is the one emitted by a dated slowest link."""
    if sort_params != LATENCY_DESC or len(filters) != 2:
        return None
    by_column = {item.get("column_id"): item for item in filters}
    if set(by_column) != {"created_at", "skill.trace_cohort"} or any(
        set(item) != {"column_id", "filter_config"} for item in filters
    ):
        return None
    date = by_column["created_at"]["filter_config"]
    if (
        set(date) - {"col_type", "filter_type", "filter_op", "filter_value"}
        or date.get("col_type", "NORMAL") != "NORMAL"
        or date.get("filter_type") != "datetime"
        or date.get("filter_op") != "between"
        or not isinstance(date.get("filter_value"), list)
        or len(date["filter_value"]) != 2
    ):
        return None
    cohort = by_column["skill.trace_cohort"]["filter_config"]
    token = cohort.get("filter_value")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    expected = {
        "col_type": "SKILL",
        "filter_op": "equals",
        "filter_type": "text",
        "filter_value": token,
    }
    return token if cohort == expected else None
