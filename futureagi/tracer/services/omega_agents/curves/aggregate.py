"""The learning curves of one Console agent, computed from its runs (learning-service PLAN
stage 6, lane P1-09; rulings 157, 158 and 377).

One concern: the arithmetic over run rows. It imports no framework, so the rules below are
testable without a database. The rows come from ``runs.py``.

A run is one trace in the agent's Observe project, as P1-01 stamps it. The rules:

- **n counts every run.** A run is completed when its root span is ``OK``, failed when it is
  ``ERROR``, and unfinished otherwise (no finishing event was recorded). Failed runs count in
  n, in cost and in latency like any other.
- **Cost is the ledger's.** It is the integer micro-USD summed over the run's LLM spans, each
  stamped with ``gen_ai.cost.total``. A run with an LLM span that lacks the stamp is unpriced.
  It is left out of every cost figure and counted, and is never filled in from a price table.
  A run with no LLM span costs zero.
- **Latency excludes approval wait.** It is the root span's duration minus the time the run
  waited on a person (``omega.task.approval_wait_ms``), never below zero. Percentiles are
  nearest-rank: the ceil(p * n / 100)-th smallest value.
- **Every figure carries its n.** That is the number of runs it was computed over (for cost,
  the priced runs). A missing or zero figure also carries the reason.
- **Below MINIMUM_N runs, headline figures are withheld.** That covers the overview tiles and
  their deltas from the previous version (the claims). Points, versions and totals are always
  given, with their n.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

# Headline figures (tiles) and their deltas need this many runs on each side. An engineering
# default for phase 1, stated in every answer so the page can say it.
MINIMUM_N = 10

COMPLETED = "completed"
FAILED = "failed"
UNFINISHED = "unfinished"

# Why a figure is missing or zero.
NO_RUNS = "no_runs"
NO_PRICED_RUNS = "no_priced_runs"
NO_MODEL_CALLS = "no_model_calls"
CHARGED_NOTHING = "charged_nothing"
NO_COMPLETED_RUNS = "no_completed_runs"
UNDER_ONE_MILLISECOND = "under_one_millisecond"
TOO_FEW_RUNS = "too_few_runs"
NO_PREVIOUS_VERSION = "no_previous_version"

_MICROS_PER_USD = Decimal(1_000_000)
_USD_STEP = Decimal("0.000001")


@dataclass(frozen=True)
class AgentRun:
    """One run of the agent: its trace's root span and the sums over its LLM spans."""

    trace_id: str
    task_id: str
    # The agent version the run used (``omega.agent.version``); None when not stamped.
    version: int | None
    started_at: datetime
    latency_ms: int
    approval_wait_ms: int
    status: str
    llm_spans: int
    unpriced_llm_spans: int
    cost_micros: int


def outcome(run: AgentRun) -> str:
    if run.status == "OK":
        return COMPLETED
    if run.status == "ERROR":
        return FAILED
    return UNFINISHED


def net_latency_ms(run: AgentRun) -> int:
    return max(0, run.latency_ms - max(0, run.approval_wait_ms))


def is_priced(run: AgentRun) -> bool:
    return run.unpriced_llm_spans == 0


def day_of(run: AgentRun) -> date:
    started = run.started_at
    if started.tzinfo is not None:
        started = started.astimezone(UTC)
    return started.date()


def _metric(value, n: int, reason: str | None) -> dict:
    return {"value": value, "n": n, "reason": reason}


def _usd(micros: int, per: int = 1, scale: int = 1) -> float:
    exact = Decimal(micros) * scale / Decimal(per) / _MICROS_PER_USD
    return float(exact.quantize(_USD_STEP, rounding=ROUND_HALF_EVEN))


def _cost_reason(priced: Sequence[AgentRun], total_micros: int) -> str | None:
    if total_micros != 0:
        return None
    if all(run.llm_spans == 0 for run in priced):
        return NO_MODEL_CALLS
    return CHARGED_NOTHING


def _cost_metrics(runs: Sequence[AgentRun]) -> dict:
    priced = [run for run in runs if is_priced(run)]
    total = sum(run.cost_micros for run in priced)
    n = len(priced)
    if not runs:
        missing = _metric(None, 0, NO_RUNS)
        return dict.fromkeys(
            ("totalCostUsd", "costPerRunUsd", "costPer1kRunsUsd"), missing
        )
    if not priced:
        missing = _metric(None, 0, NO_PRICED_RUNS)
        return dict.fromkeys(
            ("totalCostUsd", "costPerRunUsd", "costPer1kRunsUsd"), missing
        )
    reason = _cost_reason(priced, total)
    return {
        "totalCostUsd": _metric(_usd(total), n, reason),
        "costPerRunUsd": _metric(_usd(total, per=n), n, reason),
        "costPer1kRunsUsd": _metric(_usd(total, per=n, scale=1000), n, reason),
    }


def nearest_rank(sorted_values: Sequence[int], percent: int) -> int:
    """The ceil(percent * n / 100)-th smallest of ``sorted_values`` (n > 0, 0 < percent <= 100)."""
    rank = (percent * len(sorted_values) + 99) // 100
    return sorted_values[max(rank, 1) - 1]


def _latency(latencies: Sequence[int], percent: int) -> dict:
    if not latencies:
        return _metric(None, 0, NO_RUNS)
    value = nearest_rank(latencies, percent)
    return _metric(value, len(latencies), UNDER_ONE_MILLISECOND if value == 0 else None)


def summarize(runs: Sequence[AgentRun]) -> dict:
    """Every figure for one group of runs (a day and version, a version, or the window)."""
    outcomes = [outcome(run) for run in runs]
    completed = outcomes.count(COMPLETED)
    n = len(runs)
    latencies = sorted(net_latency_ms(run) for run in runs)
    if n == 0:
        success = _metric(None, 0, NO_RUNS)
    else:
        success = _metric(completed / n, n, None if completed else NO_COMPLETED_RUNS)
    priced = sum(1 for run in runs if is_priced(run))
    return {
        "n": n,
        "completed": completed,
        "failed": outcomes.count(FAILED),
        "unfinished": outcomes.count(UNFINISHED),
        "pricedRuns": priced,
        "unpricedRuns": n - priced,
        "approvalWaitExcludedMs": sum(run.latency_ms for run in runs) - sum(latencies),
        "outcomeSuccessRate": success,
        **_cost_metrics(runs),
        "p50LatencyMs": _latency(latencies, 50),
        "p95LatencyMs": _latency(latencies, 95),
    }


def _version_key(version: int | None) -> tuple[bool, int]:
    return (version is None, version or 0)


def _grouped(runs: Iterable[AgentRun], key) -> dict:
    groups: dict = {}
    for run in runs:
        groups.setdefault(key(run), []).append(run)
    return groups


def points(runs: Sequence[AgentRun]) -> list[dict]:
    """One point per (UTC day, version) with runs, by day then version (unversioned last)."""
    groups = _grouped(runs, lambda run: (day_of(run), run.version))
    ordered = sorted(groups, key=lambda key: (key[0], _version_key(key[1])))
    return [
        {
            "day": day.isoformat(),
            "version": version,
            **summarize(groups[(day, version)]),
        }
        for day, version in ordered
    ]


def versions(runs: Sequence[AgentRun]) -> list[dict]:
    """One summary per version with runs, oldest first (unversioned last)."""
    groups = _grouped(runs, lambda run: run.version)
    rows = []
    for version in sorted(groups, key=_version_key):
        days = sorted(day_of(run) for run in groups[version])
        rows.append(
            {
                "version": version,
                "firstDay": days[0].isoformat(),
                "lastDay": days[-1].isoformat(),
                **summarize(groups[version]),
            }
        )
    return rows


TILES = ("costPer1kRunsUsd", "p50LatencyMs", "outcomeSuccessRate")


def _headline(metric: dict, minimum_n: int) -> dict:
    if metric["value"] is not None and metric["n"] < minimum_n:
        return _metric(None, metric["n"], TOO_FEW_RUNS)
    return metric


def _delta(name: str, current, previous):
    if name == "costPer1kRunsUsd":
        return float(Decimal(repr(current)) - Decimal(repr(previous)))
    return current - previous


def _tile(name: str, current: dict, previous: dict | None, minimum_n: int) -> dict:
    shown = _headline(current[name], minimum_n)
    tile = {**shown, "previousValue": None, "previousN": 0, "delta": None}
    if previous is None:
        return {**tile, "deltaReason": NO_PREVIOUS_VERSION}
    before = _headline(previous[name], minimum_n)
    tile.update(previousValue=before["value"], previousN=before["n"])
    if shown["value"] is None or before["value"] is None:
        return {**tile, "deltaReason": shown["reason"] or before["reason"]}
    return {
        **tile,
        "delta": _delta(name, shown["value"], before["value"]),
        "deltaReason": None,
    }


def overview(runs: Sequence[AgentRun], minimum_n: int = MINIMUM_N) -> dict:
    """The Overview tiles for the latest version with runs, each with its delta from the
    highest earlier version with runs. Unversioned runs make tiles only when no run is
    versioned."""
    groups = _grouped(runs, lambda run: run.version)
    numbered = sorted(version for version in groups if version is not None)
    if numbered:
        current_version = numbered[-1]
        previous_version = numbered[-2] if len(numbered) > 1 else None
    else:
        current_version, previous_version = None, None
    current = summarize(groups.get(current_version, []))
    previous = (
        summarize(groups[previous_version]) if previous_version is not None else None
    )
    return {
        "version": current_version,
        "previousVersion": previous_version,
        "tiles": {name: _tile(name, current, previous, minimum_n) for name in TILES},
    }


def curves(runs: Sequence[AgentRun], minimum_n: int = MINIMUM_N) -> dict:
    """Everything the agent's curves show for one window of runs."""
    return {
        "minimumN": minimum_n,
        "totals": summarize(runs),
        "versions": versions(runs),
        "points": points(runs),
        "overview": overview(runs, minimum_n),
    }
