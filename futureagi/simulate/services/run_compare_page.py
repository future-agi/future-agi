"""One page of an N-run compare: scenario rows, each holding every run's calls.

Every per-run number is the v3 calls page's own, read from the same cached rows
and filtered by the Python twin of its query, so a run's line here equals what
that run's detail page shows for the same search and filters.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.services.run_call_match import (
    STATUS_FILTERS,
    filter_rows,
    read_call_facts,
    sub_goal_facet,
)
from simulate.services.run_results_v3 import (
    build_call_rows,
    build_evaluation_catalog,
    call_dataset_rows,
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
MOVEMENT_ORDER = {"regressed": 0, "mixed": 1, "improved": 2, "same": 3}
HISTOGRAM_BANDS = 10
# The most calls a page builds: rows with many trials make for fewer rows.
MAX_PAGE_CALLS = 600


def _row_id(scenario_key: str, columns: dict[str, Any]) -> str:
    """The scenario key, plus a digest of its row-level content when it has any."""
    # A use case that only falls back to the scenario name is no content.
    if columns["goal"] == columns["scenario"] and not any(
        columns[name]
        for name in ("persona_details", "scenario_details", "ideal_outcome")
    ):
        return scenario_key
    digest = hashlib.sha1(
        json.dumps([columns[name] for name in ROW_COLUMNS], sort_keys=True).encode()
    ).hexdigest()[:12]
    return f"{scenario_key}:{digest}"


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
    calls = list(
        CallExecution.objects.filter(id__in=list(representatives))
        .select_related("scenario")
        .only(
            "id", "test_execution_id", "row_id", "scenario__name", "scenario__metadata"
        )
        .annotate(row_metadata=JsonbKeySubset("call_metadata", ROW_METADATA_KEYS))
        .order_by()
    )
    for call in calls:
        call.result_goal = goals.get(str(call.id))
    dataset_rows, dimensions = call_dataset_rows(calls)
    versions: dict[Any, dict[str, str]] = defaultdict(dict)
    shown: dict[Any, dict[str, dict[str, Any]]] = defaultdict(dict)
    for call in calls:
        run_id, key = representatives[str(call.id)]
        columns = row_level_columns(call, dataset_rows, dimensions)
        row_id = _row_id(key, columns)
        versions[run_id][key] = row_id
        shown[run_id][row_id] = {"scenario_key": key, **columns}
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
            "settled": _settled_statuses(
                rows_by_run[execution.id], settled_for[execution.id], now
            ),
            "settled_for": settled_for[execution.id],
            "stamp": stamps[execution.id],
        }
        for execution in executions
    }


def _rate(outcomes: list[str]) -> float | None:
    decided = sum(outcome in {"passed", "failed"} for outcome in outcomes)
    return outcomes.count("passed") / decided if decided else None


def _movement(baseline: float | None, rates: list[float | None]) -> str:
    worse = better = False
    if baseline is not None:
        for rate in rates:
            if rate is not None:
                worse |= rate < baseline
                better |= rate > baseline
    if worse and better:
        return "mixed"
    return "regressed" if worse else "improved" if better else "same"


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

    # Scenario rows in first-seen order: each holds every run's calls of that version.
    scenario_rows: dict[str, dict[str, Any]] = {}
    for execution in executions:
        facts = compare_facts[execution.id]
        for row in rows_by_run[execution.id]:
            row_id = facts["row_ids"][row["id"]]
            entry = scenario_rows.setdefault(
                row_id,
                {
                    "row_id": row_id,
                    "shown": facts["rows"][row_id],
                    "calls": {one.id: [] for one in executions},
                    "kept": False,
                },
            )
            entry["calls"][execution.id].append(row["id"])
            entry["kept"] |= row["id"] in matched_ids[execution.id]
    kept = [entry for entry in scenario_rows.values() if entry["kept"]]
    for entry in kept:
        outcomes = {
            run_id: _trial_outcomes(ids, compare_facts[run_id]["calls"], values[run_id])
            for run_id, ids in entry["calls"].items()
        }
        entry["movement"] = _movement(
            _rate([outcome for _, outcome in outcomes[baseline.id]]),
            [
                _rate([outcome for _, outcome in outcomes[one.id]])
                for one in executions[1:]
            ],
        )
        # Outcomes that differ, not trial counts: a rate moved, or a trial
        # ended unlike the baseline's.
        entry["changed"] = entry["movement"] != "same" or _changed(
            outcomes[baseline.id], [outcomes[one.id] for one in executions[1:]]
        )

    def scenario_order(entry: dict[str, Any]) -> tuple[Any, ...]:
        shown = entry["shown"]
        return (str(shown["scenario"]).lower(), shown["scenario_key"], entry["row_id"])

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
                    call.result_goal = values[execution.id][call_id]["result_goal"]
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
        if rows and any(str(c["id"]) not in rows[0]["scores"] for c in columns):
            rows_by_run[execution.id] = scan_call_values(
                execution, columns, "-started_at", live_evals=live_evals
            )
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
                "row_id": entry["row_id"],
                "scenario_key": entry["shown"]["scenario_key"],
                "scenario": entry["shown"]["scenario"],
                "use_case": entry["shown"]["goal"],
                "persona": entry["shown"]["persona"],
                "persona_details": entry["shown"]["persona_details"],
                "situation": entry["shown"]["scenario_details"],
                "ideal_outcome": entry["shown"]["ideal_outcome"],
                "calls": {
                    str(run_id): sorted(
                        (built[call_id] for call_id in ids if call_id in built),
                        key=_trial_order,
                    )
                    for run_id, ids in entry["calls"].items()
                },
                "movement": entry["movement"],
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
                    row_ids.setdefault(key, []).append(entry["row_id"])
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
