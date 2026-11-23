"""One page of an N-run compare: scenario rows, each holding every run's calls.

Every per-run number is the v3 calls page's own, read from the same cached rows
and filtered by the Python twin of its query, so a run's line here equals what
that run's detail page shows for the same search and filters.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from simulate.models import (
    CallExecution,
    HostedHarnessScenario,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services import run_compare
from simulate.services.harness_scenarios import scenario_fields
from simulate.services.run_call_match import (
    STATUS_FILTERS,
    filter_rows,
    read_call_facts,
    sub_goal_facet,
)
from simulate.services.run_results_v3 import (
    build_call_rows,
    build_evaluation_catalog,
    call_scenario_sources,
    harness_columns_cache_key,
    row_level_columns,
)
from simulate.services.run_results_v3_page import (
    COMPARE_FACTS_SHAPE,
    JsonbKeySubset,
    _in_group,
    _run_key_inputs,
    cached_execution_rows,
    cached_run_values,
    group_entry,
    group_label,
    load_row_calls,
    summarize_call_values,
)
from simulate.services.run_results_v3_queries import (
    GROUP_FIELDS,
    LIST_AXES,
    _group_keys,
    facet_level_labels,
    row_facets,
)
from simulate.services.run_results_v3_scan import scan_call_values
from simulate.services.run_results_v3_scoring import live_eval_specs
from simulate.services.scoring_status import (
    derive_call_scoring,
    scoring_counts,
    scoring_input_from_values,
    scoring_settled,
)

# The row-level columns a scenario version is told apart by.
ROW_COLUMNS = (
    "goal",
    "persona",
    "persona_details",
    "scenario_details",
    "ideal_outcome",
)
# The call_metadata keys ``row_level_columns`` and its scenario lookups read.
ROW_METADATA_KEYS = (
    "persona",
    "row_data",
    "row_id",
    "use_case",
    "goal",
    "harness_scenario_key",
)
MOVEMENT_ORDER = {
    "regressed": 0,
    "mixed": 1,
    "improved": 2,
    "inconclusive": 3,
    "same": 4,
}
# The scenario fields a run plays: an edit to any of them changes what it tested.
PLAYED_FIELDS = (
    "instruction",
    "use_case",
    "persona",
    "tests",
    "sub_goals",
    "branch",
    "max_turns",
    "background_noise",
)
HISTOGRAM_BANDS = 10
# The most calls a page builds: rows with many trials make for fewer rows.
MAX_PAGE_CALLS = 600


def _shown_digest(columns: dict[str, Any]) -> str:
    """A digest of a call's row-level content; empty when it has none."""
    # A use case that only falls back to the scenario name is no content.
    if columns["goal"] == columns["scenario"] and not any(
        columns[name]
        for name in ("persona_details", "scenario_details", "ideal_outcome")
    ):
        return ""
    return hashlib.sha1(
        json.dumps([columns[name] for name in ROW_COLUMNS], sort_keys=True).encode()
    ).hexdigest()[:12]


def _played_digest(scenario: HostedHarnessScenario | None) -> str | None:
    """A digest of the scenario content a call played; ``None`` off the harness."""
    if scenario is None:
        return None
    # Normalised as a suite member is, an empty field as absent, so an identity
    # row and its member agree.
    played = scenario_fields(
        {name: getattr(scenario, name) or None for name in PLAYED_FIELDS},
        scenario.scenario_key,
    )
    played["sub_goals"] = sorted(
        json.dumps(one, sort_keys=True) for one in played["sub_goals"] or []
    )
    return hashlib.sha1(
        json.dumps([played[name] for name in PLAYED_FIELDS], sort_keys=True).encode()
    ).hexdigest()


def _settled_statuses(
    rows: list[dict[str, Any]], settled_for: tuple[Any, ...], now: datetime
) -> dict[str, str]:
    """The calls' scoring statuses that no clock can move, under ``settled_for``."""
    visible_ids, runnable_ids, run_status = settled_for
    settled: dict[str, str] = {}
    for row in rows:
        inp = scoring_input_from_values(row)
        scoring = derive_call_scoring(
            inp,
            visible_ids=visible_ids,
            runnable_ids=runnable_ids,
            run_status=run_status,
            now=now,
        )
        if scoring.status != "pending" and scoring_settled(inp, scoring):
            settled[row["id"]] = scoring.status
    return settled


def _read_compare_facts(
    executions: list[TestExecution],
    rows_by_run: dict[Any, list[dict[str, Any]]],
    stamps: dict[Any, str],
    settled_for: dict[Any, tuple[Any, ...]],
    now: datetime,
) -> dict[Any, dict[str, Any]]:
    """Each run's call facts and scenario versions, from one call per scenario key.

    Each run also keeps its settled scoring statuses, the inputs they hold for
    and the stamp of the rows they derive from.
    """
    facts = read_call_facts([execution.id for execution in executions])
    representatives: dict[str, tuple[Any, str]] = {}
    for execution in executions:
        by_key: dict[str, str] = {}
        for call_id, one in facts[execution.id].items():
            key = one["scenario_key"] or call_id
            by_key[key] = min(by_key.get(key, call_id), call_id)
        representatives.update(
            {call_id: (execution.id, key) for key, call_id in by_key.items()}
        )
    goals = {
        row["id"]: row["result_goal"] for rows in rows_by_run.values() for row in rows
    }
    # Only what the row-level columns read.
    calls: list[CallExecution] = list(
        CallExecution.objects.filter(id__in=list(representatives))
        .select_related("scenario")
        .only(
            "id", "test_execution_id", "row_id", "scenario__name", "scenario__metadata"
        )
        .annotate(row_metadata=JsonbKeySubset("call_metadata", ROW_METADATA_KEYS))
        .order_by()
    )
    for call in calls:
        call.result_goal = goals.get(str(call.id))  # type: ignore[attr-defined]
    scenarios, dimensions = call_scenario_sources(calls)
    pinned = run_compare.run_versions([execution.id for execution in executions])
    versions: dict[Any, dict[str, str]] = defaultdict(dict)
    shown: dict[Any, dict[str, dict[str, Any]]] = defaultdict(dict)
    digests: dict[Any, dict[str, str | None]] = defaultdict(dict)
    for call in calls:
        run_id, key = representatives[str(call.id)]
        columns = row_level_columns(call, scenarios, dimensions)
        # One row per scenario: an edit marks the run, it never splits the row.
        row_id = key
        versions[run_id][key] = row_id
        shown[run_id][row_id] = {"scenario_key": key, **columns}
        # An unpinned run reads today's identity row, not what it played, so
        # it is only compared with another unpinned run, on what it showed.
        if pinned[str(run_id)]["suite"]:
            played = _played_digest(scenarios.get(str(call.id))) or ""
            digests[run_id][row_id] = f"pinned:{played}:{_shown_digest(columns)}"
        else:
            digests[run_id][row_id] = f"unpinned:{_shown_digest(columns)}"
    # A representative deleted since the facts were read: its key alone.
    for run_id, key in representatives.values():
        if key not in versions[run_id]:
            versions[run_id][key] = key
            shown[run_id][key] = {
                "scenario_key": key,
                "scenario": key,
                "goal": key,
                **dict.fromkeys(ROW_COLUMNS[1:]),
            }
    return {
        execution.id: {
            "calls": facts[execution.id],
            "row_ids": {
                call_id: versions[execution.id][one["scenario_key"] or call_id]
                for call_id, one in facts[execution.id].items()
            },
            "rows": shown[execution.id],
            "digests": digests[execution.id],
            "environment": (pinned[str(execution.id)]["environment"] or {}).get("id"),
            "settled": _settled_statuses(
                rows_by_run[execution.id], settled_for[execution.id], now
            ),
            "settled_for": settled_for[execution.id],
            "stamp": stamps[execution.id],
        }
        for execution in executions
    }


def _counts(outcomes: list[str]) -> tuple[int, int]:
    """``(passed, decided)``: the calls that passed, and those that passed or failed."""
    passed = outcomes.count("passed")
    return passed, passed + outcomes.count("failed")


def _movement(baseline: tuple[int, int], candidates: list[tuple[int, int]]) -> str:
    """The row's movement: each candidate's verdict against the baseline.

    A side with no passed or failed call has nothing to compare, as before.
    """
    verdicts = {
        run_compare.verdict(*candidate, *baseline)[0]
        for candidate in candidates
        if candidate[1] and baseline[1]
    }
    worse = run_compare.REGRESSED in verdicts
    better = run_compare.IMPROVED in verdicts
    if worse and better:
        return "mixed"
    if worse or better:
        return "regressed" if worse else "improved"
    return "inconclusive" if run_compare.INCONCLUSIVE in verdicts else "same"


def _edited_runs(
    entry: dict[str, Any],
    executions: list[TestExecution],
    compare_facts: dict[Any, dict[str, Any]],
    sub_goal_digests: dict[str, Any],
) -> list[str]:
    """The runs that played this row's scenario unlike the baseline did."""
    baseline = executions[0].id
    if not entry["calls"][baseline]:
        return []
    names = {
        name
        for run_id, ids in entry["calls"].items()
        for call_id in ids
        for name in compare_facts[run_id]["calls"][call_id]["facet_sub_goals"]
    }
    ours = compare_facts[baseline]
    digest = ours["digests"].get(entry["row_id"])
    edited = []
    for execution in executions[1:]:
        if not entry["calls"][execution.id]:
            continue
        theirs = compare_facts[execution.id]
        other = theirs["digests"].get(entry["row_id"])
        comparable = (
            digest and other and digest.split(":", 1)[0] == other.split(":", 1)[0]
        )
        if (comparable and digest != other) or run_compare.changed_sub_goals(
            names,
            sub_goal_digests.get(ours["environment"]),
            sub_goal_digests.get(theirs["environment"]),
        ):
            edited.append(str(execution.id))
    return edited


def _changed(
    baseline: list[tuple[Any, str]], others: list[list[tuple[Any, str]]]
) -> bool:
    """Whether a run's trial ended unlike the baseline's, paired as Show diff pairs.

    Each ``(trial_index, outcome)`` meets the baseline's same trial, else its
    first; a trial only one side played is not a change. Without the baseline,
    the runs that played the row ended differently.
    """
    if not baseline:
        return len({frozenset(o for _, o in calls) for calls in others if calls}) > 1
    by_trial = dict(reversed(baseline))
    return any(
        outcome != by_trial.get(trial, baseline[0][1])
        for calls in others
        for trial, outcome in calls
    )


def _histograms(
    rows: list[dict[str, Any]], columns: list[dict[str, str]]
) -> dict[str, list[int]]:
    histograms = {}
    for column in columns:
        bands = [0] * HISTOGRAM_BANDS
        for row in rows:
            score = row["scores"].get(str(column["id"]))
            if score is not None:
                bands[
                    min(max(int(score * HISTOGRAM_BANDS), 0), HISTOGRAM_BANDS - 1)
                ] += 1
        histograms[str(column["id"])] = bands
    return histograms


def _page_starts(kept: list[dict[str, Any]], page_size: int) -> list[int]:
    """Each page's first row: ``page_size`` rows, fewer when they hold many calls."""
    starts: list[int] = []
    rows = calls = 0
    for index, entry in enumerate(kept):
        size = sum(map(len, entry["calls"].values()))
        if not starts or rows == page_size or calls + size > MAX_PAGE_CALLS:
            starts.append(index)
            rows = calls = 0
        rows += 1
        calls += size
    return starts


def _trial_order(call: dict[str, Any]) -> tuple[Any, ...]:
    trial = call["trial_index"]
    return (
        not isinstance(trial, int),
        trial if isinstance(trial, int) else 0,
        call["id"],
    )


def _js_round(value: float) -> int:
    """Half up, as the cells round (``Math.round``), not Python's half even."""
    return math.floor(value + 0.5)


def _to_tenth(value: float) -> float:
    return _js_round(value * 10) / 10


def _at(field: str, precision: Any = None) -> Any:
    """Reads a call's ``field`` at ``precision``; None stays None."""

    def read(call: dict[str, Any]) -> Any:
        value = call[field]
        return value if value is None or precision is None else precision(value)

    return read


# Show diff's measures: each call's value at the precision its cell shows, so
# equal-looking cells draw no arrow.
DIFF_MEASURES: dict[str, Any] = {
    # Seconds to whole ms, then to the 100 ms the cell shows.
    "duration": _at(
        "duration_seconds", lambda s: _js_round(_js_round(s * 1000) / 100) * 100
    ),
    "csat": _at("csat", lambda v: _to_tenth(_to_tenth(v))),
    "turns": _at("turn_count"),
    "latency": _at("latency_ms", _js_round),
    "stop_latency": _at("avg_stop_time_after_interruption", _js_round),
    "ai_interruptions": _at("ai_interruption_count"),
    "tokens": _at("tokens", _js_round),
}
# Measures where less is better; every other one, and every score, is better higher.
LOWER_IS_BETTER = {
    "latency",
    "stop_latency",
    "ai_interruptions",
    "duration",
    "tokens",
    "turns",
}


def _delta(
    after: float | None, before: float | None, lower_is_better: bool = False
) -> dict[str, Any] | None:
    """A change from ``before`` to ``after``; None when a side is missing or equal."""
    if after is None or before is None or after == before:
        return None
    up = after > before
    return {
        "up": up,
        "better": up != lower_is_better,
        "change": round(abs(after - before), 4),
    }


def _diff_status(call: dict[str, Any]) -> str:
    # An unmeasured call reads as inconclusive, as its cell does.
    return call["outcome"] or "inconclusive"


def _eval_points(call: dict[str, Any]) -> dict[str, int]:
    """Each scored evaluation's score as whole percentage points."""
    points: dict[str, int] = {}
    for entry in call["evaluations"]:
        score = entry["score"]
        value = entry.get("value")
        if score is None and isinstance(value, dict):
            raw = value.get("score")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                score = raw if raw <= 1 else raw / 100
        if score is not None:
            points[str(entry["id"])] = _js_round(score * 100)
    return points


def _met_sub_goals(call: dict[str, Any]) -> int:
    return sum(goal.get("passed") is True for goal in call["sub_goal_results"])


def _call_diff(call: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Every change on one call against the baseline's call."""
    after, before = _eval_points(call), _eval_points(base)
    return {
        "was": (
            _diff_status(base) if _diff_status(call) != _diff_status(base) else None
        ),
        "sub_goals": (
            _delta(_met_sub_goals(call), _met_sub_goals(base))
            if call["sub_goal_results"] or base["sub_goal_results"]
            else None
        ),
        "measures": {
            key: _delta(read(call), read(base), key in LOWER_IS_BETTER)
            for key, read in DIFF_MEASURES.items()
        },
        "evals": {
            eval_id: _delta(after.get(eval_id), before.get(eval_id))
            for eval_id in {**after, **before}
        },
    }


def _with_diffs(
    calls: dict[str, list[dict[str, Any]]], baseline_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Each run's calls with ``diff`` against the baseline's call of the same
    trial, else its first; null on the baseline and with nothing to compare."""
    base_calls = calls.get(baseline_id) or []

    def base_for(call: dict[str, Any]) -> dict[str, Any] | None:
        same = (b for b in base_calls if b["trial_index"] == call["trial_index"])
        return next(same, base_calls[0] if base_calls else None)

    return {
        run_id: [
            {
                **call,
                "diff": (
                    None
                    if run_id == baseline_id or (base := base_for(call)) is None
                    else _call_diff(call, base)
                ),
            }
            for call in run_calls
        ]
        for run_id, run_calls in calls.items()
    }


def _trial_outcomes(
    call_ids: list[str],
    facts: dict[str, Any],
    values: dict[str, dict[str, Any]],
) -> list[tuple[Any, str]]:
    """``(trial_index, outcome)`` of these calls, in trial order."""
    calls = sorted(
        (
            {"id": call_id, "trial_index": facts[call_id]["trial_index"]}
            for call_id in call_ids
        ),
        key=_trial_order,
    )
    return [
        (call["trial_index"], values[call["id"]]["result_outcome"]) for call in calls
    ]


def build_compare_page(
    executions: list[TestExecution], query: dict[str, Any]
) -> dict[str, Any]:
    """The compare page for ``executions``, the first being the baseline.

    Each run's ``execution_summary`` is its whole-run summary, for the caller's
    execution payload.
    """
    baseline = executions[0]
    # Runs of one environment share its run test, so its evals are read once.
    live_evals = live_eval_specs(baseline.run_test_id)
    runnable_ids = {
        eval_id for eval_id, live in live_evals.items() if live.config.mapping
    }
    now = timezone.now()
    configs = list(
        SimulateEvalConfig.objects.filter(
            run_test_id=baseline.run_test_id, deleted=False
        ).values("id", "name")
    )
    column_keys = {
        execution.id: harness_columns_cache_key(execution) for execution in executions
    }
    cached_columns = cache.get_many([key for key in column_keys.values() if key])
    catalogs = {
        execution.id: build_evaluation_catalog(
            execution,
            configs=configs,
            cached_columns=cached_columns.get(column_keys[execution.id]),
        )
        for execution in executions
    }
    columns_by_run = {run_id: catalog[0] for run_id, catalog in catalogs.items()}
    key_inputs = _run_key_inputs(executions)
    # Each run's rows stamp, which the facts derived from those rows hold.
    stamps: dict[Any, str] = {}
    rows_by_run = cached_execution_rows(
        executions, columns_by_run, live_evals, key_inputs=key_inputs, stamps=stamps
    )
    # What a run's settled statuses were derived under; any change discards them.
    settled_for = {
        execution.id: (
            tuple(sorted(map(str, catalogs[execution.id][1]))),
            tuple(sorted(map(str, runnable_ids))),
            execution.status,
        )
        for execution in executions
    }
    compare_facts = cached_run_values(
        executions,
        lambda missed: _read_compare_facts(
            missed, rows_by_run, stamps, settled_for, now
        ),
        shape=COMPARE_FACTS_SHAPE,
        columns_by_run=columns_by_run,
        key_inputs=key_inputs,
        # Facts derived from rows cached since under another stamp are read again.
        valid=lambda run_id, facts: facts["stamp"] == stamps[run_id],
    )

    matched = {
        run_id: filter_rows(rows, compare_facts[run_id]["calls"], query)
        for run_id, rows in rows_by_run.items()
    }
    matched_ids = {
        run_id: {row["id"] for row in rows} for run_id, rows in matched.items()
    }
    values = {
        run_id: {row["id"]: row for row in rows} for run_id, rows in rows_by_run.items()
    }

    # Rows in first-seen order, one per scenario trial: each holds every run's
    # call of that trial, so runs line up call for call.
    scenario_rows: dict[str, dict[str, Any]] = {}
    for execution in executions:
        facts = compare_facts[execution.id]
        for row in rows_by_run[execution.id]:
            row_id = facts["row_ids"][row["id"]]
            trial = facts["calls"][row["id"]]["trial_index"]
            entry_id = row_id if trial is None else f"{row_id}:{trial}"
            entry = scenario_rows.setdefault(
                entry_id,
                {
                    "id": entry_id,
                    "row_id": row_id,
                    "trial_index": trial,
                    "shown": facts["rows"][row_id],
                    "calls": {one.id: [] for one in executions},
                    "kept": False,
                },
            )
            entry["calls"][execution.id].append(row["id"])
            entry["kept"] |= row["id"] in matched_ids[execution.id]
    kept = [entry for entry in scenario_rows.values() if entry["kept"]]
    # A verdict needs every trial of the scenario, so it is the scenario's,
    # shown on each of its trial rows.
    scenario_calls: dict[str, dict[Any, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for entry in scenario_rows.values():
        for run_id, ids in entry["calls"].items():
            scenario_calls[entry["row_id"]][run_id].extend(ids)
    movements: dict[str, str] = {}
    for row_id, calls_by_run in scenario_calls.items():
        trials = {
            run_id: _trial_outcomes(
                calls_by_run[run_id], compare_facts[run_id]["calls"], values[run_id]
            )
            for run_id in (one.id for one in executions)
        }
        movements[row_id] = _movement(
            _counts([outcome for _, outcome in trials[baseline.id]]),
            [
                _counts([outcome for _, outcome in trials[one.id]])
                for one in executions[1:]
            ],
        )
    for entry in kept:
        outcomes = {
            run_id: _trial_outcomes(ids, compare_facts[run_id]["calls"], values[run_id])
            for run_id, ids in entry["calls"].items()
        }
        entry["movement"] = movements[entry["row_id"]]
        # Outcomes that differ, not trial counts: a rate moved, or a trial
        # ended unlike the baseline's.
        entry["changed"] = entry["movement"] != "same" or _changed(
            outcomes[baseline.id], [outcomes[one.id] for one in executions[1:]]
        )

    def scenario_order(entry: dict[str, Any]) -> tuple[Any, ...]:
        shown = entry["shown"]
        return (
            str(shown["scenario"]).lower(),
            shown["scenario_key"],
            entry["row_id"],
            *_trial_order({"trial_index": entry["trial_index"], "id": ""}),
        )

    if query.get("ordering") == "scenario":
        kept.sort(key=scenario_order)
    else:
        kept.sort(
            key=lambda entry: (
                MOVEMENT_ORDER[entry["movement"]],
                not entry["changed"],
                *scenario_order(entry),
            )
        )
    page, page_size = query["page"], query["page_size"]
    starts = _page_starts(kept, page_size)
    page_entries = (
        kept[starts[page - 1] : (starts + [len(kept)])[page]]
        if page <= len(starts)
        else []
    )

    # Every call on the page, read once, then built per run as its detail page does.
    by_id = load_row_calls(
        [
            call_id
            for entry in page_entries
            for ids in entry["calls"].values()
            for call_id in ids
        ],
        {execution.id: execution for execution in executions},
    )
    # The scenario sources key by call and run, so one read serves every run.
    scenario_sources = call_scenario_sources(list(by_id.values()))
    built: dict[str, dict[str, Any]] = {}
    columns_shown = {}
    for execution in executions:
        calls = []
        for entry in page_entries:
            for call_id in entry["calls"][execution.id]:
                if (call := by_id.get(call_id)) is not None:
                    call.result_goal = values[execution.id][call_id]["result_goal"]  # type: ignore[attr-defined]
                    calls.append(call)
        columns, live_eval_ids = catalogs[execution.id]
        if calls:
            call_rows, columns = build_call_rows(
                execution,
                calls,
                columns,
                live_eval_ids,
                live_evals,
                runnable_ids=runnable_ids,
                now=now,
                scenario_sources=scenario_sources,
            )
            built.update({row["id"]: row for row in call_rows})
        columns_shown[execution.id] = columns
        rows = rows_by_run[execution.id]
        # The page may add harness columns the cached rows predate: rescan the
        # run, as the calls page's groups re-read, so groups and charts score them.
        # Only the scores are taken, so the page stays the one its rows built.
        if rows and any(str(c["id"]) not in rows[0]["scores"] for c in columns):
            scores = {
                row["id"]: row["scores"]
                for row in scan_call_values(
                    execution, columns, "-started_at", live_evals=live_evals
                )
            }
            rows_by_run[execution.id] = [
                {**row, "scores": scores.get(row["id"], row["scores"])} for row in rows
            ]
            matched[execution.id] = filter_rows(
                rows_by_run[execution.id], compare_facts[execution.id]["calls"], query
            )
            # Statuses settled from the earlier rows no longer stand for these.
            compare_facts[execution.id] = {
                **compare_facts[execution.id],
                "settled": {},
            }

    # The page's rows read the scenario sources their calls just did, so an
    # edit since the facts were cached shows alike on both.
    for entry in page_entries:
        ids: list[str] = next((ids for ids in entry["calls"].values() if ids), [])
        if (call := by_id.get(min(ids, default=""))) is not None:
            entry["shown"] = {
                **entry["shown"],
                **row_level_columns(call, *scenario_sources),
            }

    # Sub-goal definitions are read only when the runs span environment versions.
    environments = {facts["environment"] for facts in compare_facts.values()} - {None}
    sub_goal_digests = (
        run_compare.sub_goal_digests(environments) if len(environments) > 1 else {}
    )

    # Without a status filter the status chips count the matching calls.
    status_filtered = any(
        (query.get("filters") or {}).get(key) for key in STATUS_FILTERS
    )
    runs = []
    for execution in executions:
        run_matched = matched[execution.id]
        page_ids = {
            call_id
            for entry in page_entries
            for call_id in entry["calls"][execution.id]
            if call_id in matched_ids[execution.id] and call_id in built
        }
        execution_summary = summarize_call_values(rows_by_run[execution.id])
        # Unfiltered, the matched rows are the run's own list: summarise it once.
        matched_summary = (
            execution_summary
            if run_matched is rows_by_run[execution.id]
            else summarize_call_values(run_matched)
        )
        runs.append(
            {
                "execution_summary": execution_summary,
                "is_baseline": execution.id == baseline.id,
                "summary": {
                    **matched_summary,
                    "scoring": scoring_counts(
                        run_matched,
                        visible_ids=catalogs[execution.id][1],
                        runnable_ids=runnable_ids,
                        run_status=execution.status,
                        now=now,
                        # The page's rows win over the settled statuses.
                        derived={
                            **(
                                compare_facts[execution.id]["settled"]
                                if compare_facts[execution.id]["settled_for"]
                                == settled_for[execution.id]
                                else {}
                            ),
                            **{
                                call_id: built[call_id]["scoring_status"]
                                for call_id in page_ids
                            },
                        },
                    ),
                },
                "status_counts": (
                    summarize_call_values(
                        filter_rows(
                            rows_by_run[execution.id],
                            compare_facts[execution.id]["calls"],
                            query,
                            drop_status=True,
                        )
                    )
                    if status_filtered
                    else matched_summary
                )["outcomes"],
                "histograms": _histograms(run_matched, columns_shown[execution.id]),
            }
        )

    evaluation_columns: dict[str, dict[str, str]] = {}
    for execution in executions:
        for column in columns_shown[execution.id]:
            seen = evaluation_columns.get(str(column["id"]))
            # A sub-goal in one run and an evaluation in another reads as the
            # evaluation, so no run's scores hide under the sub-goal columns.
            if seen is None or (
                seen["kind"] == "sub_goal" and column["kind"] == "evaluation"
            ):
                evaluation_columns[str(column["id"])] = column
    facets = row_facets([row for rows in rows_by_run.values() for row in rows])
    facets["sub_goal"] = sub_goal_facet(
        one for facts in compare_facts.values() for one in facts["calls"].values()
    )
    count = len(kept)
    return {
        "baseline_execution_id": str(baseline.id),
        "runs": runs,
        "evaluation_columns": list(evaluation_columns.values()),
        "facets": facets,
        "level_labels": facet_level_labels(facets),
        "changed_count": sum(entry["changed"] for entry in kept),
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, len(starts)),
        "groups": _page_groups(
            executions,
            kept,
            page_entries,
            matched,
            matched_ids,
            query.get("group_by"),
            columns_shown,
        ),
        "rows": [
            {
                "row_id": entry["id"],
                "trial_index": entry["trial_index"],
                "scenario_key": entry["shown"]["scenario_key"],
                "scenario": entry["shown"]["scenario"],
                "use_case": entry["shown"]["goal"],
                "persona": entry["shown"]["persona"],
                "persona_details": entry["shown"]["persona_details"],
                "situation": entry["shown"]["scenario_details"],
                "ideal_outcome": entry["shown"]["ideal_outcome"],
                "calls": _with_diffs(
                    {
                        str(run_id): sorted(
                            (built[call_id] for call_id in ids if call_id in built),
                            key=_trial_order,
                        )
                        for run_id, ids in entry["calls"].items()
                    },
                    str(baseline.id),
                ),
                "movement": entry["movement"],
                "edited_run_ids": _edited_runs(
                    entry, executions, compare_facts, sub_goal_digests
                ),
            }
            for entry in page_entries
        ],
    }


def _page_groups(
    executions: list[TestExecution],
    kept: list[dict[str, Any]],
    page_entries: list[dict[str, Any]],
    matched: dict[Any, list[dict[str, Any]]],
    matched_ids: dict[Any, set[str]],
    group_by: str | None,
    columns_by_run: dict[Any, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """The page rows' groups: a row joins every group one of its matching calls is in.

    Each run's entry is its calls page's group for the same key and filters.
    """
    if group_by not in GROUP_FIELDS or not page_entries:
        return []
    field = GROUP_FIELDS[group_by]
    buckets: dict[Any, dict[str, list[dict[str, Any]]]] = {}
    keys_of: dict[Any, dict[str, list[str]]] = {}
    # The keys a row of a list axis is only loosely in, as when stored with spaces.
    loose: dict[Any, set[str]] = defaultdict(set)
    for execution in executions:
        run_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        run_keys = {}
        for row in matched[execution.id]:
            keys = _group_keys(group_by, row[field])
            run_keys[row["id"]] = keys
            if group_by in LIST_AXES and row[field] != keys:
                loose[execution.id].update(keys)
            for key in keys:
                run_buckets[key].append(row)
        buckets[execution.id] = run_buckets
        keys_of[execution.id] = run_keys
    # Every kept row a group holds, on any page.
    row_counts: Counter[str] = Counter()
    for entry in kept:
        row_counts.update(
            {
                key
                for run_id, ids in entry["calls"].items()
                for call_id in ids
                if call_id in matched_ids[run_id]
                for key in keys_of[run_id][call_id]
            }
        )
    row_ids: dict[str, list[str]] = {}
    page_call_ids: dict[str, dict[Any, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for entry in page_entries:
        for run_id, ids in entry["calls"].items():
            for call_id in ids:
                if call_id not in matched_ids[run_id]:
                    continue
                for key in keys_of[run_id][call_id]:
                    page_call_ids[key][run_id].append(call_id)
                    row_ids.setdefault(key, []).append(entry["id"])
    groups = []
    for key, joined in row_ids.items():
        runs = {}
        for execution in executions:
            if key not in buckets[execution.id]:
                continue
            runs[str(execution.id)] = group_entry(
                buckets[execution.id][key],
                group_by,
                key,
                page_call_ids[key].get(execution.id, []),
                columns_by_run[execution.id],
                # A list axis keys loosely; the calls page holds only exact members.
                members=(
                    [
                        row
                        for row in buckets[execution.id][key]
                        if _in_group(group_by, row[field], key)
                    ]
                    if key in loose[execution.id]
                    else buckets[execution.id][key]
                ),
            )
        groups.append(
            {
                "key": key,
                "label": group_label(group_by, key),
                "row_ids": list(dict.fromkeys(joined)),
                "row_count": row_counts[key],
                "runs": runs,
            }
        )
    return sorted(
        groups,
        key=lambda group: (
            -sum(one["total"] for one in group["runs"].values()),
            group["label"].lower(),
        ),
    )
