"""Shared, fully scoped population for R3 variants and R5 analysis.

select_results serves R3; select_analysis_population is the P04 interface.
rows are in filtered/sorted order; examples and plans retain the original
population. No page is applied here. Unknown provenance fails closed.
"""

import hashlib
import json
import operator
from copy import deepcopy
from dataclasses import dataclass, field
from math import frexp, fsum, ldexp, sqrt

from django.db.models import Count

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentRunItem
from model_hub.services.experiment_run_writes import _planned, _canonical, RunWriteError
from model_hub.services.persisted_run_comparison import _bindings
from model_hub.services.evaluator_comparison_identity import (
    comparison_signature,
    comparison_trial,
    project_selection,
)
from model_hub.services.results_catalog import (
    ResultsReadError, bounded_list, checked_runs, scoped_dataset, pinned_baseline, metric_shape,
)
from model_hub.services.results_root_causes import pass_rule_keys


@dataclass
class ResultsSelection:
    dataset: object
    runs: list
    baseline_run_id: str | None
    plans: dict
    examples: list
    items: dict
    bindings: dict
    trial_numbers: dict
    rows: list = field(default_factory=list)
    selection_id: str = ""
    # Rows worse, better and changed against the baseline, counted over the
    # filtered rows before a show applies; None without a baseline to compare.
    show_counts: dict | None = None


OPS = {"eq": operator.eq, "=": operator.eq, "ne": operator.ne, "!=": operator.ne,
       "lt": operator.lt, "<": operator.lt, "lte": operator.le, "<=": operator.le,
       "gt": operator.gt, ">": operator.gt, "gte": operator.ge, ">=": operator.ge}


def _compare(value, op, expected):
    if value is None:
        return False
    try:
        return OPS[op](value, expected)
    except TypeError:
        return False


def _run_ids(pill, selection):
    ids = [str(r.id) for r in selection.runs]
    run = pill.get("run", "any_candidate")
    if run == "any_run":
        return ids
    if run == "any_candidate":
        return [rid for rid in ids if rid != selection.baseline_run_id]
    if run not in ids:
        raise ResultsReadError("Filter run not found in this selection", 404)
    return [run]


def _flaky(cell, rules=None):
    """Some tries of the cell pass and some fail, by the matrix's own rule.

    `rules`: the metric ids whose evaluations have a pass/fail rule
    (pass_rule_keys); without them every score is graded.
    """
    from model_hub.services.results_matrix import trial_value

    outcomes = [trial_value(trial, rules=rules) for trial in cell["trials_detail"]]
    return True in outcomes and False in outcomes


def _rules(selection, rid):
    """The pass/fail rule of the run's pinned evaluations."""
    return pass_rule_keys(selection.bindings[rid])


def _matches(row, pill, selection):
    kind = pill["type"]
    if kind == "rows":
        return row["example_id"] in pill["ids"]
    if kind == "meta":
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        return _compare(metadata.get(pill["key"]), pill["op"], pill["value"])
    if kind == "split":
        meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
        names = meta.get("split_names", [])
        if isinstance(names, str):
            names = [names]
        if not names:
            names = [meta.get("split_name", "__none__")]
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise ResultsReadError("Persisted split metadata is malformed")
        return bool(set(names) & set(pill["values"]))
    for rid in _run_ids(pill, selection):
        cell = row["results"][rid]
        if kind == "error" and any(t["status"] in {"error", "infra"} for t in cell["trials_detail"]):
            return True
        if kind == "flaky" and _flaky(cell, _rules(selection, rid)):
            return True
        if kind == "fails" and any(t["scores"].get(pill["metric"], {}).get("passed") is False
                                   for t in cell["trials_detail"]):
            return True
        if kind == "score":
            value = cell["scores"].get(pill["metric"], {}).get("value")
            if _compare(value, pill["op"], pill["value"]):
                return True
        if kind in {"latency", "cost"}:
            key = "latency_ms" if kind == "latency" else "cost_usd"
            if _compare(cell[key], pill["op"], pill["value"]):
                return True
    return False


def _compared(selection):
    """The baseline and the selected runs compared with it, or None."""
    baseline = selection.baseline_run_id
    candidates = [str(run.id) for run in selection.runs if str(run.id) != baseline]
    return (baseline, candidates) if baseline and candidates else None


def _movements(selection):
    """How each compared run moved from the baseline, row by row.

    Returns a function of a row that lists ("eval", change) for each
    evaluation whose value moved past its threshold (any move of a pass/fail
    evaluation, more than 0.05 otherwise) and ("system", change) for each
    spend or latency that moved by at least 10%. The change is positive when
    the run got better and negative when it got worse, by the metric's pinned
    direction (lower is better for spend and latency); an evaluation with no
    direction gives None. Unknown/missing values never move. Trials are
    averaged first, as in the grid. Each row is read once.
    """
    from model_hub.services.results_grid import result_cell

    baseline, candidates = _compared(selection)
    numbers = {}
    for eid, number in sorted(selection.plans[baseline]):
        numbers.setdefault(eid, []).append(number)
    definitions = {
        key: metric_shape(pin) for key, pin in selection.bindings[baseline].items()
    }
    baseline_run = []
    read = {}

    def base_cell(row):
        cell = row["results"].get(baseline)
        if cell is not None:
            return cell
        if not baseline_run:
            baseline_run.append(
                next((run for run in selection.runs if str(run.id) == baseline), None)
                or checked_runs(selection.dataset, [baseline])[0]
            )
        eid = row["example_id"]
        return result_cell(
            [
                comparison_trial(
                    selection.items.get((baseline, eid, n)),
                    n,
                    baseline_run[0],
                    selection.bindings[baseline],
                )
                for n in numbers[eid]
            ]
        )

    def moved(row):
        if row["example_id"] in read:
            return read[row["example_id"]]
        base = base_cell(row)
        movements = []
        for rid in candidates:
            candidate = row["results"][rid]
            for key, definition in definitions.items():
                bv = base["scores"].get(key, {}).get("value")
                cv = candidate["scores"].get(key, {}).get("value")
                if bv is None or cv is None:
                    continue
                delta = cv - bv
                threshold = 0 if definition["output_type"] == "pass_fail" else 0.05
                if abs(delta) > threshold + 1e-12:
                    direction = definition["higher_is_better"]
                    movements.append(
                        (
                            "eval",
                            None
                            if direction is None
                            else delta
                            if direction
                            else -delta,
                        )
                    )
            for key in ("cost_usd", "latency_ms"):
                bv, cv = base.get(key), candidate.get(key)
                if (
                    bv is not None
                    and cv is not None
                    and cv != bv
                    and abs(cv - bv) >= abs(bv) * 0.1
                ):
                    movements.append(("system", bv - cv))
        read[row["example_id"]] = movements
        return movements

    return moved


def _shown(show, movements):
    """Whether a row's movements put it in a show: Worse (regressed) and Better
    (improved) when an evaluation with a direction got worse or better, Changed
    when any evaluation moved. Spend and latency do not count here."""
    changes = [change for kind, change in movements if kind == "eval"]
    if show == "regressed":
        return any(change is not None and change < 0 for change in changes)
    if show == "improved":
        return any(change is not None and change > 0 for change in changes)
    return bool(changes)


def regressed_first(rows, selection, moved=None):
    """Stable worse/better/rest groups over the complete assessed population.

    Unknown/missing values never move a row. Metric direction comes from its
    pinned definition, and a metric with no direction never moves a row here.
    Trials are averaged first, as in the grid. System cost and latency use
    the shared 10% movement threshold (lower is better).
    """
    if _compared(selection) is None:
        raise ResultsReadError(
            "Regressed sort requires a baseline and a candidate", 400
        )
    moved = moved or _movements(selection)

    def rank(row):
        changes = [change for _, change in moved(row) if change is not None]
        return (
            0
            if any(v < 0 for v in changes)
            else 1
            if any(v > 0 for v in changes)
            else 2
        )

    return sorted(rows, key=rank)


# The cell value a per-run sort reads.
RUN_SORT_FIELDS = {"latency": "latency_ms", "cost": "cost_usd"}
# The sorts by how one run's tries of a row spread, most spread first.
SPREAD_SORTS = ("variance", "flakiness")
# The shows by how a compared run moved from the baseline (Worse, Better,
# Changed); the query's show choices list them too.
MOVEMENT_SHOWS = ("regressed", "improved", "changed")


def _checked_sort(sort, selection):
    """What a sort by one run's values names, checked against the selection.

    delta:<metric>:<run>:asc|desc, score:<metric>:<run>:asc|desc and
    variance:<metric>:<run> name a metric (its key may hold colons) and a
    run; a per-run sort and flakiness:<run> name a run. A delta needs a
    baseline and a selected run other than it. The others need a selected
    run, and the baseline is one when it is selected. The metric is one the
    run scores. Returns None for every other sort.
    """
    kind, _, rest = sort.partition(":")
    if kind in ("delta", "score"):
        parts = rest.rsplit(":", 2)
        if len(parts) != 3 or not parts[0] or parts[2] not in ("asc", "desc"):
            raise ResultsReadError("Unsupported sort", 400)
        metric, run, direction = parts
    elif kind == "variance":
        metric, _, run = rest.rpartition(":")
        if not metric or not run:
            raise ResultsReadError("Unsupported sort", 400)
        direction = "desc"
    elif kind in RUN_SORT_FIELDS or kind == "flakiness":
        if not rest or ":" in rest:
            raise ResultsReadError("Unsupported sort", 400)
        metric, run, direction = None, rest, "desc"
    else:
        return None
    selected = {str(r.id) for r in selection.runs}
    baseline = selection.baseline_run_id
    if kind == "delta":
        if not baseline:
            raise ResultsReadError("The sort needs a baseline run to compare with", 400)
        if run == baseline or run not in selected:
            raise ResultsReadError("The sort names a run that is not compared with the baseline here", 400)
    elif run not in selected:
        raise ResultsReadError("The sort names a run that is not in this selection", 400)
    if metric is not None and metric not in selection.bindings[baseline if kind == "delta" else run]:
        raise ResultsReadError("The sort names a metric these runs do not score", 400)
    return kind, metric, run, direction


def _known_first(rows, value, descending):
    """Rows by value(row), smallest first unless descending.

    A row whose value is None comes after the others in its existing order.
    Equal values keep their existing order.
    """
    def rank(row):
        known = value(row)
        if known is None:
            return (1, 0)
        return (0, -known if descending else known)

    return sorted(rows, key=rank)


def delta_sorted(rows, selection, metric, run, direction):
    """Rows by one run's difference from the baseline on one metric.

    The difference is the run's value minus the baseline's, trials averaged
    first as in the grid (a pass/fail metric's value is its pass rate), signed
    by the metric's pinned direction so that getting worse is negative. "asc"
    puts the largest drop first and "desc" the largest gain. A row with an
    unknown value on either side, and every row of a metric with no direction,
    comes after the known rows in its existing order. Equal differences keep
    their existing order.
    """
    from model_hub.services.results_grid import result_cell

    baseline = selection.baseline_run_id
    higher = metric_shape(selection.bindings[baseline][metric])["higher_is_better"]
    if higher is None:
        return list(rows)
    baseline_run = next((r for r in selection.runs if str(r.id) == baseline), None)
    if baseline_run is None:
        baseline_run = checked_runs(selection.dataset, [baseline])[0]
    numbers = {}
    for eid, number in sorted(selection.plans[baseline]):
        numbers.setdefault(eid, []).append(number)

    def gain(row):
        eid = row["example_id"]
        base = row["results"].get(baseline)
        if base is None:
            base = result_cell(
                [
                    comparison_trial(
                        selection.items.get((baseline, eid, n)),
                        n,
                        baseline_run,
                        selection.bindings[baseline],
                    )
                    for n in numbers[eid]
                ]
            )
        bv = base["scores"].get(metric, {}).get("value")
        cv = row["results"][run]["scores"].get(metric, {}).get("value")
        if bv is None or cv is None:
            return None
        return cv - bv if higher else bv - cv

    return _known_first(rows, gain, direction == "desc")


def run_sorted(rows, kind, metric, run, direction):
    """Rows by one run's own value.

    "score" reads the run's value for the metric, trials averaged first as in
    the grid: "asc" puts the lowest first and "desc" the highest. A per-run
    sort reads the run's latency or spend for the row, highest first. A row
    with an unknown value comes after the known rows in its existing order.
    Equal values keep their existing order.
    """
    if kind == "score":
        def value(row):
            return row["results"][run]["scores"].get(metric, {}).get("value")
    else:
        field = RUN_SORT_FIELDS[kind]

        def value(row):
            return row["results"][run][field]

    return _known_first(rows, value, direction == "desc")


def _spread(values):
    """The population standard deviation of finite values.

    It orders values as their population variance does. Values that are all
    the same have none. Otherwise they are first scaled by the power of two
    that brings the largest under 1, so no step can overflow. The mean is
    their correctly rounded sum (fsum) over their count, and the deviations
    from it are corrected by their own mean before they are squared and
    summed, so the rounding of the mean adds no spread of its own. Past the
    largest float the spread reads as infinity; it never raises.
    """
    first = values[0]
    if all(v == first for v in values):
        return 0.0
    exponent = frexp(max(abs(v) for v in values))[1]
    scaled = [ldexp(v, -exponent) for v in values]
    count = len(scaled)
    average = fsum(scaled) / count
    deviations = [v - average for v in scaled]
    drift = fsum(deviations) / count
    corrected = [d - drift for d in deviations]
    # Squared by multiplying, which never raises (a power can, on some
    # platforms, for a result below the smallest normal float).
    deviation = sqrt(fsum(d * d for d in corrected) / count)
    # Undo the scaling by multiplying, which reads as infinity, never raises.
    return deviation * ldexp(1.0, exponent - 1) * 2


def spread_sorted(rows, selection, kind, metric, run):
    """Rows by how far apart one run's tries of the row are, most first.

    "variance" reads the metric's assessed value on each try and orders by
    their population variance, read as their population standard deviation
    (_spread) so that no finite value overflows. "flakiness" reads each
    try's outcome by the matrix's own pass/fail rule (trial_value with the
    run's pinned rules) and orders by 1 - |2p - 1|, p being the share of
    the judged tries that pass: 1 when they split evenly, 0 when they
    agree. Errored, unfinished and unjudged tries are left out. A row with
    fewer than 2 such tries comes after the others in its existing order,
    and equal values keep their existing order.
    """
    from model_hub.services.results_matrix import trial_value

    if kind == "variance":

        def value(row):
            values = [
                float(score["value"])
                for score in (
                    trial["scores"].get(metric, {})
                    for trial in row["results"][run]["trials_detail"]
                )
                if score.get("status") == "assessed" and score.get("value") is not None
            ]
            if len(values) < 2:
                return None
            return _spread(values)
    else:
        rules = _rules(selection, run)

        def value(row):
            outcomes = [
                trial_value(trial, rules=rules)
                for trial in row["results"][run]["trials_detail"]
            ]
            judged = [
                outcome for outcome in outcomes if outcome is True or outcome is False
            ]
            if len(judged) < 2:
                return None
            # 1 - |2p - 1| over whole counts, so equal splits are equal values.
            return (len(judged) - abs(2 * judged.count(True) - len(judged))) / len(
                judged
            )

    return _known_first(rows, value, True)


def filter_rows(selection, query):
    rows = selection.rows
    all_ids = {row["example_id"] for row in rows}
    metric_ids = {key for pins in selection.bindings.values() for key in pins}
    pills = query.get("filters", [])
    # Validation precedes filtering, including when the population is empty.
    for pill in pills:
        if pill["type"] == "rows" and not set(pill["ids"]) <= all_ids:
            raise ResultsReadError("Example not found in the immutable plan", 404)
        if "metric" in pill and pill["metric"] not in metric_ids:
            raise ResultsReadError("Evaluator not found in this selection", 404)
        if "run" in pill:
            _run_ids(pill, selection)
    if query.get("example_id") and str(query["example_id"]) not in all_ids:
        raise ResultsReadError("Example not found in the immutable plan", 404)
    sort = query.get("sort", "order")
    named = _checked_sort(sort, selection)
    show = query.get("show", "all")
    moved = _movements(selection) if _compared(selection) else None
    if show in MOVEMENT_SHOWS and moved is None:
        raise ResultsReadError(
            "The show needs a baseline and a run to compare with it", 400
        )
    for pill in pills:
        rows = [row for row in rows if _matches(row, pill, selection)]
    search = query.get("search", "").casefold()
    if search:
        rows = [
            row
            for row in rows
            if search
            in json.dumps(
                {
                    "inputs": row["inputs"],
                    "expected": row["expected"],
                    "label": row["row_label"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).casefold()
        ]
    # Worse, Better and Changed count the rows the filters and search keep,
    # whichever show applies. Only the grid's summary carries them, and only
    # from the cells the selection already holds: with the baseline outside
    # the selected runs there are no counts, and the read stays as it was.
    counted = (
        moved is not None
        and query.get("shape", "grid") != "matrix"
        and any(str(run.id) == selection.baseline_run_id for run in selection.runs)
    )
    selection.show_counts = (
        {
            name: sum(1 for row in rows if _shown(name, moved(row)))
            for name in MOVEMENT_SHOWS
        }
        if counted
        else None
    )
    if show in MOVEMENT_SHOWS:
        rows = [row for row in rows if _shown(show, moved(row))]
    elif show == "errors":
        rows = [
            row
            for row in rows
            if any(
                t["status"] in {"error", "infra"}
                for c in row["results"].values()
                for t in c["trials_detail"]
            )
        ]
    elif show == "failed":
        rows = [
            row
            for row in rows
            if any(
                score["passed"] is False
                for cell in row["results"].values()
                for t in cell["trials_detail"]
                for score in t["scores"].values()
            )
        ]
    elif show == "flaky":
        rows = [
            row
            for row in rows
            if any(
                _flaky(c, _rules(selection, rid)) for rid, c in row["results"].items()
            )
        ]
    if named and named[0] == "delta":
        rows = delta_sorted(rows, selection, *named[1:])
    elif named and named[0] in SPREAD_SORTS:
        rows = spread_sorted(rows, selection, *named[:3])
    elif named:
        rows = run_sorted(rows, *named)
    elif sort == "regressed":
        rows = regressed_first(rows, selection, moved)
    elif sort.lstrip("-") == "input":
        rows = sorted(rows, key=lambda r: (_canonical(r["inputs"]), r["example_id"]),
                      reverse=sort.startswith("-"))
    elif sort == "-order":
        rows = list(reversed(rows))
    return rows


def select_results(request, dataset_id, query):
    dataset = scoped_dataset(request, dataset_id)
    baseline, _ = pinned_baseline(dataset, query.get("baseline"))
    return _select_population(dataset, [str(rid) for rid in query["runs"]], baseline, query)


def _select_population(dataset, ids, baseline, query, *, max_items=20000, per_run_limit=None):
    from model_hub.services.results_grid import build_rows
    authorized_ids = ids + ([baseline] if baseline and baseline not in ids else [])
    runs = checked_runs(dataset, authorized_ids)
    plans, bindings, signatures = {}, {}, {}
    try:
        for run in runs:
            if per_run_limit is not None:
                meta = run.metadata if isinstance(run.metadata, dict) else {}
                envelope = meta.get("_futureagi_run_write", {})
                raw_plan = envelope.get("plan", {}) if isinstance(envelope, dict) else {}
                identities = raw_plan.get("identities") if isinstance(raw_plan, dict) else None
                if isinstance(identities, list) and len(identities) > per_run_limit:
                    raise ResultsReadError("Run plan exceeds the analysis per-run work limit.", 503)
            plans[str(run.id)] = _planned(run)
            bindings[str(run.id)] = _bindings(run)
            signatures[str(run.id)] = _canonical(comparison_signature(run))
    except RunWriteError as exc:
        raise ResultsReadError("Immutable provenance unavailable; use classic results.") from exc
    reference = runs[0]
    plan = plans[str(reference.id)]
    for run in runs[1:]:
        if (
            run.dataset_version_id != reference.dataset_version_id
            or plans[str(run.id)] != plan
            or signatures[str(run.id)] != signatures[str(reference.id)]
        ):
            raise ResultsReadError(
                "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported."
            )
    if sum(len(p) for p in plans.values()) > max_items:
        raise ResultsReadError(f"Selection exceeds {max_items} planned trials; select fewer runs.", 503)
    example_ids = {eid for eid, _ in plan}
    examples = bounded_list(DatasetVersionItem.no_workspace_objects.filter(
        dataset_version=reference.dataset_version, example_id__in=example_ids, deleted=False)
        .only("example_id", "input", "expected", "metadata", "external_id").order_by("example_id"),
        ("input", "expected", "metadata"), limit=min(10000, max_items))
    if {str(e.example_id) for e in examples} != example_ids:
        raise ResultsReadError("Example not found in the immutable version", 404)
    raw_query = ExperimentRunItem.no_workspace_objects.filter(run_id__in=authorized_ids, deleted=False)
    if per_run_limit is not None and raw_query.values("run_id").annotate(
            n=Count("id")).filter(n__gt=per_run_limit).exists():
        raise ResultsReadError("Run evidence exceeds the analysis per-run work limit.", 503)
    raw_items = bounded_list(raw_query.order_by("run_id", "example_id", "trial_number"),
        ("input", "expected", "output", "error", "score_refs", "metadata"), limit=max_items)
    items = {}
    for item in raw_items:
        rid, eid = str(item.run_id), str(item.example_id)
        key = (rid, eid, item.trial_number)
        if (eid, item.trial_number) not in plans[rid] or key in items:
            raise ResultsReadError("Recorded evidence is outside or duplicates the pinned plan")
        items[key] = item
    selected_runs = [run for run in runs if str(run.id) in ids]
    trial_numbers = {}
    for rid in ids:
        trial_numbers[rid] = {eid: [] for eid in example_ids}
        for eid, trial in sorted(plans[rid]):
            trial_numbers[rid][eid].append(trial)
    selection = ResultsSelection(dataset, selected_runs, baseline, plans, examples, items,
                                 bindings, trial_numbers)
    selection.rows = build_rows(selection)
    project_selection(selection, runs)
    if query.get("measure", "pass_at_1") != "pass_at_1" and not any(
            query["measure"] in b for b in bindings.values()):
        raise ResultsReadError("Evaluator not found in this selection", 404)
    selection.rows = filter_rows(selection, query)
    identity = {"runs": ids, "baseline": baseline,
                "rows": [r["example_id"] for r in selection.rows],
                "version": str(reference.dataset_version_id),
                "updates": [[str(r.id), str(r.updated_at)] for r in runs],
                "item_updates": [[str(i.id), str(i.updated_at)] for i in raw_items]}
    selection.selection_id = hashlib.sha256(_canonical(identity).encode()).hexdigest()
    return selection


def select_analysis_population(*, dataset, runs, filters, baseline_id, max_items):
    """Full R3 population for an already request-scoped dataset, never a page.

    Re-resolve all runs rather than trusting stale caller objects. P04's caller
    resolves baseline defaults; None/none here means no baseline. The stricter
    R3 20000-trial/10000-example ceiling still applies even if P04 asks for more.
    ResultsReadError is a DRF APIException so the existing P04 view preserves
    404/409/503; semantic filter validation uses the same DRF 400 as R3.
    """
    from model_hub.models.develop_dataset import Dataset
    from model_hub.serializers.dataset_results import filters_value
    from model_hub.serializers.results_runs import identifier
    if type(max_items) is not int or max_items < 1:
        raise ResultsReadError("max_items must be a positive integer.", 400)
    if not isinstance(runs, (list, tuple)) or not 1 <= len(runs) <= 8:
        raise ResultsReadError("Select 1-8 distinct runs.", 400)
    ids = [identifier(run.id) for run in runs]
    if len(ids) != len(set(ids)):
        raise ResultsReadError("Select 1-8 distinct runs.", 400)
    live = Dataset.no_workspace_objects.filter(pk=dataset.pk, deleted=False,
        organization_id=dataset.organization_id, workspace_id=dataset.workspace_id).first()
    if live is None:
        raise ResultsReadError("Dataset not found", 404)
    baseline = None if baseline_id in (None, "none") else identifier(baseline_id)
    query = {"filters": filters_value(deepcopy(filters))}
    selected = _select_population(live, ids, baseline, query,
                                  max_items=min(max_items, 20000), per_run_limit=10000)
    return {"example_ids": [row["example_id"] for row in selected.rows],
            "examples_total": len(selected.examples), "complete": True}
