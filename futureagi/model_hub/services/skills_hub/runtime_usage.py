"""Runtime evidence primitives shared by usage, comparison and load projections.

These functions are deliberately transport-independent. The public route migration
is not frozen; do not install parallel /runtime routes around the shared API.
Callers supply project permissions and workspace-scoped Postgres catalogues, never
names taken from telemetry. Fetches, linked versions and loads stay separate facts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from model_hub.services.skills_hub.errors import SkillsHubError

MIN_TRACES = 30
MIN_EVALUATED = 30
FAILED_REQUESTS_POINTS = Decimal("0.01")
FAILED_REQUESTS_RATIO = Decimal("1.5")
LATENCY_RATIO = Decimal("1.25")
EVAL_PASS_POINTS = Decimal("0.05")
MIN_SCOPE_COVERAGE = Decimal("0.5")
MAX_PROJECTS = 50
MAX_ROWS = 200
TOTALS_NOTE = "cohorts overlap; not additive"

AgentKey = tuple[str, str, str]


def _invalid(message: str) -> SkillsHubError:
    return SkillsHubError("invalid_runtime_query", message, status=400)


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    return result if result.is_finite() else None


@dataclass(frozen=True)
class ProjectScope:
    """Created from a server-authorized id set, not from query parameters alone."""

    project_ids: tuple[str, ...]
    unreadable_projects: int
    truncated: bool

    @classmethod
    def select(cls, *, workspace_ids, readable_ids, requested_ids=None):
        workspace = {str(value) for value in workspace_ids}
        readable = workspace.intersection(str(value) for value in readable_ids)
        if requested_ids is None:
            selected = readable
        else:
            requested = {str(value) for value in requested_ids}
            if not requested.issubset(readable):
                # Do not distinguish a foreign project from a private one.
                raise SkillsHubError(
                    "project_not_readable", "A requested project cannot be opened.", status=403
                )
            selected = requested
        return cls(
            tuple(sorted(selected)[:MAX_PROJECTS]),
            len(workspace - readable),
            len(selected) > MAX_PROJECTS,
        )


def agent_identity(*, project_id, project_name, service_name, agent_name):
    """No credential, fetch principal or user is ever an agent identity."""
    project_id = str(project_id)
    if agent_name:
        name, source = agent_name, "agent"
    elif service_name and not service_name.startswith("unknown_service"):
        name, source = service_name, "service"
    else:
        name, source = f"Unnamed agent in {project_name}", "project"
    return {
        "key": (project_id, service_name, agent_name),
        "name": name,
        "service": service_name,
        "project_id": project_id,
        "project_name": project_name,
        "name_source": source,
    }


def _changes(before: Mapping, after: Mapping) -> list[dict]:
    """Metric-level eligibility; never invent a rate for missing roots/evals."""
    if min(before["traces"], after["traces"]) < MIN_TRACES:
        return []
    values = [
        ("failed_requests", before["failed_requests"]["rate"], after["failed_requests"]["rate"]),
        ("latency_p95", before["latency_ms"]["p95"], after["latency_ms"]["p95"]),
    ]
    old_evals = {str(row["config_id"]): row for row in before.get("evals", [])}
    new_evals = {str(row["config_id"]): row for row in after.get("evals", [])}
    for config in sorted(old_evals.keys() & new_evals.keys()):
        old, new = old_evals[config], new_evals[config]
        if min(old["evaluated"], new["evaluated"]) >= MIN_EVALUATED:
            values.append((f"eval:{config}", old["pass_rate"], new["pass_rate"]))
    changes = []
    for metric, old, new in values:
        a, b = _decimal(old), _decimal(new)
        if a is None or b is None:
            continue
        if metric == "failed_requests":
            worse = b - a >= FAILED_REQUESTS_POINTS and b >= a * FAILED_REQUESTS_RATIO
            better = a - b >= FAILED_REQUESTS_POINTS and a >= b * FAILED_REQUESTS_RATIO
        elif metric == "latency_p95":
            # Strict direction avoids classifying 0 -> 0 as a regression.
            worse = b > a and b >= a * LATENCY_RATIO
            better = a > b and a >= b * LATENCY_RATIO
        else:
            worse, better = a - b >= EVAL_PASS_POINTS, b - a >= EVAL_PASS_POINTS
        changes.append({
            "metric": metric, "before": old, "after": new,
            "direction": "worse" if worse else "better" if better else "no_change",
        })
    return changes


def regression_verdict(before: Mapping[AgentKey, Mapping], after: Mapping[AgentKey, Mapping], *, complete: bool):
    """Compare the same agents only; a regression dominates any improvements.

    Trigger before/after are per-agent value lists, not averages of percentiles.
    The shared comparison projection must compute pooled metrics from trace
    identities separately; adding version/agent aggregates is not valid.
    """
    by_agent = []
    for key in sorted(before.keys() & after.keys()):
        by_agent.append({
            "agent_key": key,
            "low_sample": min(before[key]["traces"], after[key]["traces"]) < MIN_TRACES,
            "changes": _changes(before[key], after[key]),
        })
    eligible = any(row["changes"] for row in by_agent)
    trigger = None
    direction = None
    for candidate in ("worse", "better"):
        metrics = sorted({
            change["metric"] for row in by_agent for change in row["changes"]
            if change["direction"] == candidate
        }, key=lambda metric: (metric != "failed_requests", metric != "latency_p95", metric))
        if not metrics:
            continue
        direction, metric = candidate, metrics[0]
        matches = [
            (row["agent_key"], change)
            for row in by_agent for change in row["changes"]
            if change["direction"] == candidate and change["metric"] == metric
        ]
        trigger = {
            "metric": metric, "agents": len(matches),
            "before": [{"agent_key": key, "value": change["before"]} for key, change in matches],
            "after": [{"agent_key": key, "value": change["after"]} for key, change in matches],
        }
        break
    if not complete:
        verdict = "worse_so_far" if direction == "worse" else "watching"
        if direction != "worse":
            trigger = None
    else:
        verdict = direction or ("no_change" if eligible else "not_enough_data")
    return {
        "verdict": verdict, "trigger": trigger, "by_agent": by_agent,
        "so_far": not complete and eligible,
        "primary_action": "review_failed" if verdict in {"worse", "worse_so_far"} else None,
    }


def move_windows(*, moved_at: datetime, now: datetime, window: timedelta, previous_move_at=None):
    """Half-open windows. Caller selects the previous event of the SAME label."""
    dates = [moved_at, now] + ([previous_move_at] if previous_move_at is not None else [])
    if any(value.tzinfo is None or value.utcoffset() is None for value in dates):
        raise _invalid("Move timestamps must include a timezone.")
    if not timedelta(hours=1) <= window <= timedelta(days=7):
        raise _invalid("Comparison window must be between one hour and seven days.")
    if moved_at > now or (previous_move_at is not None and previous_move_at >= moved_at):
        raise _invalid("Move timestamps are out of order.")
    start, end = moved_at - window, moved_at + window
    cut = previous_move_at if previous_move_at is not None and previous_move_at > start else None
    return {
        "before": {"from": cut or start, "to": moved_at},
        "after": {"from": moved_at, "to": min(end, now)},
        "before_cut_at": cut, "ends_at": end, "complete": now >= end,
        "hours_elapsed": min((now - moved_at).total_seconds(), window.total_seconds()) / 3600,
        "hours_total": window.total_seconds() / 3600,
    }


def adoption_state(loads: Sequence[Mapping], *, to_version_id: str, context_rules: Mapping):
    """Classify verified after-window loads for ONE agent.

    Context rules are authoritative compiled links indexed by the immutable
    via_context_version_id, with values 'pinned' or 'following'. Mixed evidence
    falls back to got_old_after_move rather than asserting a single cause.
    """
    if not loads:
        return {"share": None, "not_moved_reason": "not_seen", "cached": False}
    new = [row for row in loads if str(row["skill_version_id"]) == str(to_version_id)]
    old = [row for row in loads if str(row["skill_version_id"]) != str(to_version_id)]
    reasons = set()
    for row in old:
        if context_rules.get(row.get("via_context_version_id")) == "pinned":
            reasons.add("pinned_in_context")
        elif context_rules.get(row.get("via_context_version_id")) == "following":
            reasons.add("waiting_for_context_release")
        elif row.get("requested_version") and not row.get("via_context_version_id"):
            reasons.add("asked_for_version")
        else:
            # Context snapshots resolve immutable versions too. Without their
            # compiled rule, that selector does not prove an application pin.
            reasons.add("got_old_after_move")
    reason = "got_new" if not old else next(iter(reasons)) if len(reasons) == 1 else "got_old_after_move"
    return {
        "share": len(new) / len(loads), "not_moved_reason": reason,
        # Do not label all old loads as cached when only some of them were.
        "cached": bool(old) and all(bool(row.get("cached")) for row in old),
    }


def runtime_diagnosis(*, has_projects: bool, readable_projects: int, consumers: Sequence[Mapping], loads: int):
    """Whitelisted fetch metadata only; capability comes from ST7, not a guess."""
    public = [{
        "label": row.get("label", ""), "channel": row["channel"],
        "sdk_version": row.get("sdk_version"), "tracing_mode": row.get("tracing_mode"),
        "can_record": bool(row.get("can_record")),
    } for row in consumers]
    capable = sum(row["can_record"] for row in public)
    sdk = [row for row in public if row["channel"] == "sdk" or row["sdk_version"]]
    if not has_projects:
        state = "no_tracing_project"
    elif not readable_projects:
        state = "no_readable_projects"
    elif loads:
        state = "partially_recording" if capable < len(public) else "recording"
    elif not public:
        state = "never_fetched"
    elif capable:
        state = "awaiting_loads"
    elif not sdk:
        state = "non_sdk_fetches"
    elif any(row["tracing_mode"] in {"off", "no_otel"} for row in sdk):
        state = "tracing_unavailable"
    else:
        # Missing identity is not evidence of an old SDK. Capability is checked
        # against the owner-selected release threshold for each SDK language.
        state = "sdk_cannot_record"
    return {"state": state, "consumers": public, "can_record": bool(capable)}


def library_runtime(*, skill_ids, counts: Mapping, consumers: Mapping, moves: Mapping):
    """Batch projection; no serializer-per-row database calls.

    All maps must come from the caller's authorized scope. Missing counts mean
    zero only after a successful CH read; the read adapter must propagate errors.
    The shared library view must provide this map through serializer context.
    """
    result = {}
    for skill_id in skill_ids:
        capable = any(row["can_record"] for row in consumers.get(skill_id, []))
        result[skill_id] = {
            "loaded_7d": int(counts.get(skill_id, 0)) if capable else None,
            "can_record": capable, "move": moves.get(skill_id),
        }
    return result


def core_metrics(row: Mapping):
    """Serialize the core CH aggregate; evals require the separate V8 adapter."""
    def number(key):
        value = _decimal(row[key])
        return float(value) if value is not None else None

    coverage = number("scope_coverage")
    return {
        "traces": int(row["traces"]), "loads": int(row["loads"]),
        "incomplete": int(row["incomplete"]),
        "failed_requests": {"count": int(row["failed_count"]), "rate": number("failed_rate")},
        "errors_any_span": int(row["errors_any_span"]),
        "latency_ms": {"p50": number("latency_p50"), "p95": number("latency_p95")},
        "cost": {"total": number("cost_total"), "per_trace_mean": number("cost_mean"), "per_trace_p50": number("cost_p50")},
        "tokens": {"total": int(row["tokens_total"]), "per_trace_mean": number("tokens_mean")},
        "in_scope_cost": {"total": number("scope_cost_total"), "coverage": coverage}
        if coverage is not None and Decimal(str(coverage)) >= MIN_SCOPE_COVERAGE else None,
        "cached_share": number("cached_share"),
        "load_overhead_ms_p50": number("load_overhead_ms_p50"),
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "low_sample": row["traces"] < MIN_TRACES,
    }


def read_runtime_statement(session, statement, *, max_rows=None):
    """Translate recognized unavailable reads, never substitute an empty cohort."""
    from tracer.services.clickhouse.read_budget import (
        is_clickhouse_api_read_unavailable_error,
        is_read_budget_error,
    )

    try:
        result = session.execute(statement)
    except Exception as exc:
        if is_read_budget_error(exc):
            raise SkillsHubError(
                "runtime_read_budget_exceeded", "This took too long. Try a shorter window.", status=503
            ) from exc
        if is_clickhouse_api_read_unavailable_error(exc):
            raise SkillsHubError(
                "runtime_read_unavailable", "Runtime data is temporarily unavailable.", status=503
            ) from exc
        raise
    if max_rows is not None and result.row_count > max_rows:
        raise SkillsHubError(
            "runtime_read_too_large", "Runtime data exceeds this read's limit.", status=503
        )
    return result.data


def closure_diff(before: Mapping[str, Mapping], after: Mapping[str, Mapping]):
    """Compare authorized compiled closures indexed by skill id, not by name."""
    changed = []
    for skill_id in sorted(before.keys() | after.keys()):
        old, new = before.get(skill_id), after.get(skill_id)
        if old and new and old["version_id"] == new["version_id"]:
            continue
        changed.append({
            "skill_id": skill_id, "name": (new or old)["name"],
            "from_version": old["version"] if old else None,
            "to_version": new["version"] if new else None,
            "change": "added" if old is None else "removed" if new is None else "changed",
        })
    return changed


def trace_load_projection(*, rows, failed_rows, trace_ids, project_id, catalog):
    """Page-scoped evidence hydration from live rows, enriched only from PG.

    catalog maps version id to {skill_id, name, version, digest} in the caller's
    workspace. Foreign or unknown identities get no name or navigable identity.
    Raw client-provided skill names are never used for successful loads.
    """
    trace_ids = tuple(dict.fromkeys(trace_ids))
    if len(trace_ids) > 100 or any(not isinstance(value, str) or not value for value in trace_ids):
        raise _invalid("Supply at most 100 non-empty trace ids.")
    grouped = {trace_id: {} for trace_id in trace_ids}
    failed = {trace_id: Counter() for trace_id in trace_ids}
    for row in rows:
        trace_id = row["trace_id"]
        if str(row["project_id"]) != str(project_id) or trace_id not in grouped:
            raise SkillsHubError("runtime_scope_mismatch", "Runtime read scope mismatch.", status=503)
        version_id = str(row["skill_version_id"])
        known = catalog.get(version_id)
        if known and str(known["skill_id"]) != str(row["skill_id"]):
            known = None
        digest_status = "verified" if known and known["digest"] == row["skill_digest"] else "mismatch"
        key = (str(row["skill_id"]), version_id, row["service_name"], row["agent_name"], row["requested_label"], row["evidence"], digest_status)
        item = grouped[trace_id].setdefault(key, {
            "skill_id": str(known["skill_id"]) if known else None,
            "name": known["name"] if known else "Unknown skill",
            "version_id": version_id if known else None,
            "version": known["version"] if known else None,
            "requested_label": row["requested_label"],
            "agent": (str(project_id), row["service_name"], row["agent_name"]),
            "evidence": row["evidence"], "digest_status": digest_status, "loads": 0,
            "first_seen": row["start_time"],
        })
        item["loads"] += 1
        item["first_seen"] = min(item["first_seen"], row["start_time"])
    for row in failed_rows:
        if str(row["project_id"]) != str(project_id) or row["trace_id"] not in failed:
            raise SkillsHubError("runtime_scope_mismatch", "Runtime read scope mismatch.", status=503)
        # Failed names are unverified application reports, not Hub identities.
        failed[row["trace_id"]][(row["skill_name"], row["error_type"])] += 1
    result = {}
    for trace_id, groups in grouped.items():
        items = sorted(groups.values(), key=lambda row: (row["first_seen"], row["version_id"] or "", row["agent"]))
        result[trace_id] = {
            "skills": [{key: value for key, value in row.items() if key != "first_seen"} for row in items[:8]],
            "more": max(0, len(items) - 8),
            "failed": [{"skill_name": name, "error_type": error, "loads": count} for (name, error), count in sorted(failed[trace_id].items())],
        }
    return {"traces": result}
