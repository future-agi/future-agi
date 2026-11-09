"""The independent recompute behind the curves proof (test_curves_replay.py). Plain Python over
the seeded rows: it imports nothing from the code under test and applies the rules unbounded,
with no look-back or grace window. Percentiles are nearest-rank, through ``Fraction``; money is
integer micro-USD, rounded half to even at the micro-dollar.
"""

import math
from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction

MIN_N = 10  # the answer states it; the proof checks it says 10
USD = Decimal("0.000001")


def _integer(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    return int(text) if text.lstrip("-").isdigit() else 0


def _ledger_priced(span):
    """The ledger priced this call: its ``gen_ai.cost.total`` is a number. The Python adapter
    and the collector keep a number in attrs_number but a string in attrs_string, and a bool
    is not a number."""
    value = span["span_attributes"].get("gen_ai.cost.total")
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def live_spans(rows):
    """Replacement: per span key the newest version wins, and a deleted winner is gone."""
    newest = {}
    for row in rows:
        hour = row["start_time"].replace(minute=0, second=0, microsecond=0)
        key = (
            row["project_id"],
            row["observation_type"],
            hour,
            row["trace_id"],
            row["id"],
        )
        if key not in newest or row["created_at"] >= newest[key]["created_at"]:
            newest[key] = row
    return [row for row in newest.values() if not row["deleted"]]


def recompute_runs(rows, project_id, agent_id, start, end):
    traces = defaultdict(list)
    for row in live_spans(rows):
        if row["project_id"] == project_id:
            traces[row["trace_id"]].append(row)
    runs = []
    for trace_id, spans in traces.items():
        roots = [
            s
            for s in spans
            if s["parent_span_id"] == ""
            and s["span_attributes"].get("omega.agent.id") == agent_id
        ]
        if not roots:
            continue
        root = min(roots, key=lambda s: (s["start_time"], s["id"]))
        if not start <= root["start_time"] < end:
            continue
        llm = [s for s in spans if s["observation_type"] == "llm"]
        version = _integer(root["span_attributes"].get("omega.agent.version"))
        runs.append(
            {
                "trace_id": trace_id,
                "task_id": root["span_attributes"].get("omega.task.id", ""),
                "version": version if version > 0 else None,
                "started_at": root["start_time"],
                "latency_ms": root["latency_ms"],
                "approval_wait_ms": max(
                    0,
                    _integer(
                        root["span_attributes"].get("omega.task.approval_wait_ms")
                    ),
                ),
                "status": root["status"],
                "llm_spans": len(llm),
                "unpriced_llm_spans": sum(1 for s in llm if not _ledger_priced(s)),
                "cost_micros": sum(round(s["cost"] * 1_000_000) for s in llm),
            }
        )
    return sorted(runs, key=lambda r: (r["started_at"], r["trace_id"]))


def _usd(amount):
    return float(amount.quantize(USD, rounding=ROUND_HALF_EVEN))


def _figures(runs):
    n = len(runs)
    statuses = [r["status"] for r in runs]
    completed, failed = statuses.count("OK"), statuses.count("ERROR")
    net = sorted(max(0, r["latency_ms"] - r["approval_wait_ms"]) for r in runs)
    priced = [r for r in runs if r["unpriced_llm_spans"] == 0]
    total = sum(r["cost_micros"] for r in priced)

    def metric(value, count, reason):
        return {"value": value, "n": count, "reason": reason}

    if n == 0:
        costs = dict.fromkeys(
            ("totalCostUsd", "costPerRunUsd", "costPer1kRunsUsd"),
            metric(None, 0, "no_runs"),
        )
    elif not priced:
        costs = dict.fromkeys(
            ("totalCostUsd", "costPerRunUsd", "costPer1kRunsUsd"),
            metric(None, 0, "no_priced_runs"),
        )
    else:
        reason = None
        if total == 0:
            reason = (
                "no_model_calls"
                if all(r["llm_spans"] == 0 for r in priced)
                else "charged_nothing"
            )
        k = len(priced)
        costs = {
            "totalCostUsd": metric(_usd(Decimal(total) / 1_000_000), k, reason),
            "costPerRunUsd": metric(_usd(Decimal(total) / k / 1_000_000), k, reason),
            "costPer1kRunsUsd": metric(
                _usd(Decimal(total) * 1000 / k / 1_000_000), k, reason
            ),
        }

    def percentile(p):
        if not net:
            return metric(None, 0, "no_runs")
        value = net[math.ceil(Fraction(p * len(net), 100)) - 1]
        return metric(value, len(net), "under_one_millisecond" if value == 0 else None)

    return {
        "n": n,
        "completed": completed,
        "failed": failed,
        "unfinished": n - completed - failed,
        "pricedRuns": len(priced),
        "unpricedRuns": n - len(priced),
        "approvalWaitExcludedMs": sum(r["latency_ms"] for r in runs) - sum(net),
        "outcomeSuccessRate": (
            metric(None, 0, "no_runs")
            if n == 0
            else metric(completed / n, n, None if completed else "no_completed_runs")
        ),
        **costs,
        "p50LatencyMs": percentile(50),
        "p95LatencyMs": percentile(95),
    }


def _version_order(version):
    return (version is None, version or 0)


def recompute_curves(runs):
    by_version = defaultdict(list)
    by_point = defaultdict(list)
    for r in runs:
        by_version[r["version"]].append(r)
        by_point[(r["started_at"].date(), r["version"])].append(r)
    versions = []
    for version in sorted(by_version, key=_version_order):
        days = [r["started_at"].date() for r in by_version[version]]
        versions.append(
            {
                "version": version,
                "firstDay": min(days).isoformat(),
                "lastDay": max(days).isoformat(),
                **_figures(by_version[version]),
            }
        )
    points = [
        {
            "day": day.isoformat(),
            "version": version,
            **_figures(by_point[(day, version)]),
        }
        for day, version in sorted(
            by_point, key=lambda key: (key[0], _version_order(key[1]))
        )
    ]
    numbered = sorted(v for v in by_version if v is not None)
    current = numbered[-1] if numbered else None
    previous = numbered[-2] if len(numbered) > 1 else None
    now = _figures(by_version.get(current, []))
    before = _figures(by_version[previous]) if previous is not None else None

    def shown(metric):
        if metric["value"] is not None and metric["n"] < MIN_N:
            return {"value": None, "n": metric["n"], "reason": "too_few_runs"}
        return metric

    def tile(name):
        mine = shown(now[name])
        out = {**mine, "previousValue": None, "previousN": 0, "delta": None}
        if before is None:
            return {**out, "deltaReason": "no_previous_version"}
        theirs = shown(before[name])
        out.update(previousValue=theirs["value"], previousN=theirs["n"])
        if mine["value"] is None or theirs["value"] is None:
            return {**out, "deltaReason": mine["reason"] or theirs["reason"]}
        if name == "costPer1kRunsUsd":
            delta = float(Decimal(repr(mine["value"])) - Decimal(repr(theirs["value"])))
        else:
            delta = mine["value"] - theirs["value"]
        return {**out, "delta": delta, "deltaReason": None}

    return {
        "minimumN": MIN_N,
        "totals": _figures(runs),
        "versions": versions,
        "points": points,
        "overview": {
            "version": current,
            "previousVersion": previous,
            "tiles": {
                name: tile(name)
                for name in ("costPer1kRunsUsd", "p50LatencyMs", "outcomeSuccessRate")
            },
        },
    }
