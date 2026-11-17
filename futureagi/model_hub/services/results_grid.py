"""Grid and full-row projections, with explicit missing evidence and bounds."""

from math import fsum, isfinite, ldexp, sqrt

from rest_framework.renderers import JSONRenderer

from model_hub.services.experiment_run_read import _bounded
from model_hub.services.results_catalog import (
    ResultsReadError,
    metric_definitions,
    metric_shape,
    project_run,
)
from model_hub.services.results_root_causes import (
    PASS_FAIL_OUTPUTS,
    pinned_pass_rule,
    pinned_score_passes,
)
from model_hub.services.row_failures import coded_message
from tracer.utils.trace_ids import stored_trace_id

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# A grid cell's stored failure reason; keeps a large failed run under the bound.
MAX_CELL_EXPLANATION = 2000
# The largest n "at least n of the row's tries passing" is served for.
AT_LEAST_MAX = 100
# A try that ran and failed to give an answer: it errored, or its
# infrastructure failed.
FAILED_TRIES = frozenset({"error", "infra"})
_Z = 1.96


def assessed_scores(refs, bindings):
    result = {}
    if not isinstance(refs, list):
        raise ResultsReadError("Score evidence is malformed")
    for ref in refs:
        if not isinstance(ref, dict):
            raise ResultsReadError("Score evidence is malformed")
        key = str(ref.get("metric_id", ""))
        pin = bindings.get(key)
        version = ref.get("evaluator_version_id", ref.get("version_id"))
        if key in result or not pin or str(version) != pin["evaluator_version_id"]:
            raise ResultsReadError("Score evidence does not match the pinned evaluator")
        value = ref.get("value", ref.get("score"))
        status = ref.get("status", "not_assessed")
        finite = type(value) in (bool, int, float) and isfinite(value)
        if status == "assessed" and not finite:
            status, value = "invalid", None
        if status != "assessed":
            value = None
        passed = pinned_score_passes(value, pin) if status == "assessed" else None
        result[key] = {"value": value, "status": status, "passed": passed,
                       "evaluator_version_id": str(version),
                       "explanation": ref.get("explanation")}
    for key, pin in bindings.items():
        result.setdefault(key, {"value": None, "passed": None, "status": "not_assessed",
                                "evaluator_version_id": pin["evaluator_version_id"],
                                "explanation": None})
    return result


def trial_record(item, number, run, bindings):
    if item is None:
        active = run.status in {"draft", "queued", "running"}
        return {"trial_number": number, "status": "queued" if active else "not_reached",
                "output": None, "error": None, "scores": {}, "cost_usd": None,
                "wall_ms": None, "cost": None, "latency_ms": None,
                "trace_id": None, "result_id": None,
                "carried": False, "availability": "No recorded attempt"}
    meta = item.metadata if isinstance(item.metadata, dict) else {}
    err = item.error if isinstance(item.error, dict) else {}
    status = item.status
    if status == "pending":
        status = "queued"
    elif status == "failed" or (status == "completed" and item.error is not None):
        status = "infra" if err.get("kind") in {"infra", "infrastructure"} else "error"
    elif status == "skipped":
        status = "stopped" if err.get("kind") == "stopped_at_cap" else "not_reached"
    scores = assessed_scores(item.score_refs, bindings)
    # A failed execution cannot gain an assessed-success score from a stray ref.
    if status != "completed":
        scores = {key: {**score, "value": None, "passed": None, "status": "not_assessed"}
                  for key, score in scores.items()}
    return {"trial_number": number, "status": status, "output": item.output,
            "error": item.error, "scores": scores, "cost_usd": item.cost,
            "wall_ms": item.latency_ms, "cost": item.cost, "latency_ms": item.latency_ms,
            # Runs record OpenTelemetry's 32 hex digits; trace views use the
            # span store's dashed form of the same id.
            "trace_id": stored_trace_id(item.trace_id),
            "result_id": str(item.id), "carried": meta.get("carried") is True,
            "availability": None}


def mean(values):
    """The mean of the finite values; None without one. While their plain sum
    is a finite float it is the sum divided, as before. Past the largest float
    the values are scaled down by a power of two that keeps every partial sum
    in range, averaged, kept between the smallest and largest value (where
    the mean lies, whatever the rounding) and scaled back, so it stays finite."""
    nums = [v for v in values if type(v) in (bool, int, float) and isfinite(v)]
    if not nums:
        return None
    total = sum(nums)
    if not isinstance(total, float) or isfinite(total):
        return total / len(nums)
    shift = len(nums).bit_length() + 1
    scaled = fsum(ldexp(v, -shift) for v in nums) / len(nums)
    low, high = ldexp(min(nums), -shift), ldexp(max(nums), -shift)
    return ldexp(min(max(scaled, low), high), shift)


def _midpoint(low, high):
    """Halfway between two finite numbers: their sum halved, as before,
    while that sum is a finite float (a sum of whole numbers is exact).
    Past the largest float both have the same sign and are large, so
    halving each first is exact and cannot overflow."""
    total = low + high
    if not isinstance(total, float) or isfinite(total):
        return total / 2
    return low / 2 + high / 2


def median(values):
    """The middle value (halfway between the two middle ones for an even
    count), of the finite values only; None without one."""
    nums = sorted(v for v in values if type(v) in (bool, int, float) and isfinite(v))
    if not nums:
        return None
    middle = len(nums) // 2
    return nums[middle] if len(nums) % 2 else _midpoint(nums[middle - 1], nums[middle])


def failed_try_message(trials):
    """The first failed try's coded sentence; None when no failed try has a
    code. The grid's cells render it as given, so it is only ever a sentence
    a code chose (row_failures), never a stored text or object."""
    for t in trials:
        if t["status"] in ("error", "infra"):
            message = coded_message(t.get("error"))
            if message:
                return message
    return None


def first_failure_message(evidence):
    """A run's first failed try's coded sentence, in example and trial order;
    None when no failed try has a code."""
    for _, item in sorted(evidence.items(), key=lambda pair: pair[0]):
        if item.status == "failed" or (item.status == "completed" and item.error is not None):
            message = coded_message(item.error)
            if message:
                return message
    return None


def result_cell(trials):
    states = {t["status"] for t in trials}
    state = next((s for s in ("running", "queued", "infra", "error", "stopped", "not_reached")
                  if s in states), "completed")
    keys = {key for t in trials for key in t["scores"]}
    scores = {}
    for key in sorted(keys):
        known = [t["scores"][key] for t in trials if key in t["scores"]
                 and t["scores"][key]["status"] == "assessed"]
        scores[key] = {"value": mean([v["value"] for v in known]), "n": len(known),
                       "status": "assessed" if known else "not_assessed",
                       "rule": "assessed_trial_mean", "total_trials": len(trials),
                       # Tries whose evaluator ran and failed, so the cell can say so.
                       "error_trials": sum(1 for t in trials
                                           if t["scores"].get(key, {}).get("status") == "error")}
        # Preserve each try's verdict; the numeric mean is not a verdict.
        scores[key]["trials"] = [t["scores"].get(key, {}).get("passed") for t in trials]
        # A cell no try scored carries its first failed try's
        # stored reason, as stored (bounded here; the full row keeps it whole).
        stored = next((t["scores"][key].get("explanation") for t in trials
                       if t["scores"].get(key, {}).get("status") == "error"
                       and isinstance(t["scores"][key].get("explanation"), str)
                       and t["scores"][key]["explanation"].strip()), None)
        if stored and not known:
            scores[key]["explanation"] = stored[:MAX_CELL_EXPLANATION]
    completed = [t for t in trials if t["status"] == "completed"]
    first = completed[0] if completed else (trials[0] if trials else {})
    cost = sum(t["cost_usd"] for t in trials) if trials and all(
        t["cost_usd"] is not None for t in trials) else None
    return {"status": state, "output": first.get("output"), "scores": scores,
            "error": failed_try_message(trials),
            "output_trial_number": first.get("trial_number"), "result_id": first.get("result_id"), "trials": len(trials),
            "trials_detail": trials, "trace_ids": [t["trace_id"] for t in trials],
            "trace_summary": [{"status": t["status"], "trial_number": t["trial_number"],
                               "carried": t["carried"]} for t in trials],
            "cost_usd": cost, "cost": cost,
            "latency_ms": mean([t["wall_ms"] for t in trials]),
            "carried": any(t["carried"] for t in trials)}


def native_example_view(selection):
    """Read a native experiment's examples by column name, with Expected.

    A native experiment stores each example as {"values": {<source column id>:
    value}} and no expected (native_run_binding.persist_snapshot_identity).
    The runner reads that stored shape, so it stays as it is. This read names
    each value by its captured column and shows the experiment's Expected
    answers column (its column_id) as Expected instead of as an input. The
    create and edit paths point column_id at the snapshot copy of the column,
    so it is read by the source column id it was copied from.
    Returns None when the selection is not one native capture.
    """
    from model_hub.models.dataset_versions import DatasetVersion
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentsTable

    version_ids = {getattr(run, "dataset_version_id", None) for run in selection.runs}
    if len(version_ids) != 1 or None in version_ids:
        return None
    version = DatasetVersion.no_workspace_objects.filter(
        pk=next(iter(version_ids))).only("id", "metadata").first()
    meta = version.metadata if version is not None and isinstance(version.metadata, dict) else {}
    columns = meta.get("columns")
    capture = meta.get("capture")
    if capture not in {"native-snapshot-v1", "http-append-v1"} or not isinstance(
        columns, dict
    ):
        return None
    if capture == "http-append-v1":
        from model_hub.models.native_experiment_runs import NativeRunBinding
        from model_hub.services.native_run_binding import validate_run_references

        bindings = NativeRunBinding.objects.filter(run__in=selection.runs)
        if bindings.count() != len(selection.runs):
            return None
        for binding in bindings:
            validate_run_references(
                binding, binding.run, selection.dataset, retired_ok=True
            )
            if columns != binding.snapshot.column_map or meta.get("snapshot_id") != str(
                binding.snapshot_id
            ):
                return None
    names = {str(pk): name for pk, name in Column.no_workspace_objects.filter(
        id__in=[str(v) for v in columns.values()]).values_list("id", "name")}
    labels = {str(source): names.get(str(snapshot)) for source, snapshot in columns.items()}
    experiment_ids = {run.experiment_id for run in selection.runs
                      if getattr(run, "experiment_id", None)}
    picked = set(ExperimentsTable.no_workspace_objects.filter(id__in=experiment_ids)
                 .values_list("column_id", flat=True)) if experiment_ids else set()
    # A snapshot column reads as the source column the stored values use; a
    # source column id (older experiments) stays as it is.
    source_of = {str(snapshot): str(source) for source, snapshot in columns.items()}
    if {str(cid) for cid in picked if cid is not None} - set(source_of) - set(labels):
        # An experiment created on an earlier version shares the version
        # another experiment saved, whose columns name that experiment's
        # copies; its own copy is read through its own snapshot's map.
        from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
        for column_map in NativeExperimentSnapshot.objects.filter(
                experiment_id__in=experiment_ids).values_list("column_map", flat=True):
            if isinstance(column_map, dict):
                for source, snapshot in column_map.items():
                    source_of.setdefault(str(snapshot), str(source))
    # Each experiment's pick, read as a source column; one column when they agree.
    resolved = {None if cid is None else source_of.get(str(cid), str(cid)) for cid in picked}
    expected_id = next(iter(resolved)) if len(resolved) == 1 and None not in resolved else None
    no_column = picked == {None}
    order = {str(cid): i for i, cid in enumerate(
        getattr(selection.dataset, "column_order", None) or [])}

    def label(key, taken):
        name = labels.get(key) or key
        return f"{name} ({key})" if name in taken else name

    def view(example):
        stored = example.input
        if (example.expected is not None or not isinstance(stored, dict)
                or set(stored) != {"values"} or not isinstance(stored["values"], dict)):
            return example.input, {"value": example.expected}
        values = stored["values"]
        inputs = {}
        for key in sorted(values, key=lambda k: order.get(k, len(order))):
            if key != expected_id:
                inputs[label(key, inputs)] = values[key]
        if expected_id is not None and expected_id in labels:
            # "primary" names the expected column, as every reader expects
            # (the row drawer's Compare tab, the Output diff, export).
            name = label(expected_id, {})
            value = values.get(expected_id)
            # A blank or missing expected cell is no expected value, as for
            # any row; the experiment still has its expected column, so the
            # row drawer says "No expected value", not "no reference column".
            if value is None or (isinstance(value, str) and not value.strip()):
                return inputs, {"value": None, "primary": name}
            return inputs, {"value": {name: value}, "primary": name}
        if no_column:
            return inputs, {"value": None, "reason": "no_reference_column"}
        return inputs, {"value": None}

    return view


def build_rows(selection):
    rows = []
    view = native_example_view(selection) if selection.runs else None
    for position, example in enumerate(selection.examples):
        eid = str(example.example_id)
        results = {}
        for run in selection.runs:
            rid = str(run.id)
            numbers = selection.trial_numbers[rid][eid]
            trials = [
                trial_record(
                    selection.items.get((rid, eid, n)), n, run, selection.bindings[rid]
                )
                for n in numbers
            ]
            results[rid] = result_cell(trials)
        inputs, expected = (
            view(example) if view else (example.input, {"value": example.expected})
        )
        rows.append(
            {
                "example_id": eid,
                "row_order": position,
                "row_label": example.external_id or str(position + 1),
                "inputs": inputs,
                "expected": expected,
                "values": inputs if isinstance(inputs, dict) else {"input": inputs},
                "metadata": example.metadata,
                "results": results,
            }
        )
    from model_hub.services.results_feedback import project_feedback

    project_feedback(rows, selection.runs)
    return rows


def progress_by_run(runs, *, items=None):
    """Example counters from planned terminal/assessed trials, never claims.

    Raw trial counts stay in counts/recorded; the FE progress readers count
    examples. A row is done only after every planned trial is terminal, and
    scored only after every pinned evaluator assessed every successful trial.
    """
    from model_hub.models.experiments import ExperimentRunItem
    from model_hub.services.experiment_run_writes import RunWriteError, _planned
    from model_hub.services.persisted_run_comparison import _bindings
    from model_hub.services.results_catalog import bounded_list
    if items is None:
        items = bounded_list(ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[run.id for run in runs], deleted=False).only(
                "run_id", "example_id", "trial_number", "status", "error", "score_refs"),
            ("error", "score_refs"))
    grouped = {str(run.id): {} for run in runs}
    for item in items:
        rid = str(item.run_id)
        if rid in grouped:
            identity = (str(item.example_id), item.trial_number)
            if identity in grouped[rid]:
                raise ResultsReadError("Recorded progress duplicates the pinned plan")
            grouped[rid][identity] = item
    result = {}
    statuses = {"draft": "NotStarted", "queued": "Queued", "running": "Running",
                "completed": "Completed", "failed": "Failed", "cancelled": "Cancelled"}
    for run in runs:
        rid = str(run.id)
        evidence = grouped[rid]
        status = ("PartialCompleted" if run.status == "partial" and run.completed_at is not None
                  else statuses.get(run.status, run.status))
        progress = {"status": status, "unit": "examples",
                    "total": None, "done": None, "scored": None, "errors": None,
                    "recorded": len(evidence), "recorded_unit": "trials",
                    # Lifted to the run's own first_error by project_run.
                    "first_error": first_failure_message(evidence)}
        result[rid] = progress
        try:
            plan = _planned(run)
        except RunWriteError:
            progress["availability"] = "Immutable run plan unavailable"
            continue
        if not set(evidence) <= plan:
            raise ResultsReadError("Recorded progress is outside the pinned plan")
        pins = _bindings(run)
        examples = {}
        for eid, number in plan:
            examples.setdefault(eid, []).append(evidence.get((eid, number)))
        done = scored = errors = 0
        for trials in examples.values():
            terminal = all(t is not None and t.status in {"completed", "failed", "skipped"}
                           for t in trials)
            done += int(terminal)
            errors += int(any(t is not None and (t.status == "failed" or (
                t.status == "completed" and t.error is not None)) for t in trials))
            assessed = bool(pins) and all(t is not None and t.status == "completed"
                and t.error is None and all(s["status"] == "assessed" for s in
                    assessed_scores(t.score_refs, pins).values()) for t in trials)
            scored += int(assessed)
        progress.update(total=len(examples), done=done, scored=scored, errors=errors,
                        trial_total=len(plan), availability=None)
    return result


def tries_per_row(numbers):
    """The most tries a row of a run's plan holds ({example id: trial
    numbers}); 1 for a plan with none."""
    return max((len(trials) for trials in numbers.values()), default=0) or 1


def wilson_interval(rate, n):
    """The Wilson 95% interval of a share of n rows, [low, high]; None
    without rows, or when the rate is not a share (outside 0..1). The same
    interval the page draws for a share."""
    if (
        rate is None
        or n <= 0
        or type(rate) not in (bool, int, float)
        or not isfinite(rate)
        or not 0 <= rate <= 1
    ):
        return None
    z2 = _Z * _Z
    denominator = 1 + z2 / n
    centre = (rate + z2 / (2 * n)) / denominator
    half = (_Z / denominator) * sqrt(rate * (1 - rate) / n + z2 / (4 * n * n))
    return [max(0.0, centre - half), min(1.0, centre + half)]


def mean_interval(values):
    """Mean +/- 1.96 sd / sqrt(n) of row values (sample sd), [low, high];
    None for fewer than 2 rows, or when the values are too large for it to
    be a finite number. The same interval the page draws for a mean."""
    if len(values) < 2:
        return None
    average = mean(values)
    # A product, not a power: a float power past the float range raises,
    # a product becomes inf and is caught below.
    spread = sum((v - average) * (v - average) for v in values)
    if not isfinite(spread):
        return None
    half = _Z * sqrt(spread / (len(values) - 1)) / sqrt(len(values))
    low, high = average - half, average + half
    return [low, high] if isfinite(low) and isfinite(high) else None


def row_tries(trials, key, binary=False):
    """One row's tries on a metric, read for the per-try aggregates.

    Returns (values, judged, tried):
    values  the metric's assessed value on each try it scored;
    judged  the verdict (True/False) of each scored try the run's pass rule
            gave one;
    tried   judged, plus a failed verdict for each try that errored or whose
            infrastructure failed: a try that did not pass did not pass.
    A try still queued or running, never reached, stopped, or not scored is
    in none of them. With `binary` (a pass/fail evaluation), an assessed
    value other than 0 or 1 is no pass/fail evidence: that try is unknown,
    in none of them either, whatever verdict it carries.
    """
    values, judged, tried = [], [], []
    for trial in trials:
        status = trial.get("status")
        if status in FAILED_TRIES:
            tried.append(False)
            continue
        if status != "completed":
            continue
        score = (trial.get("scores") or {}).get(key) or {}
        value = score.get("value")
        if (
            score.get("status") != "assessed"
            or type(value) not in (bool, int, float)
            or not isfinite(value)
            or (binary and value not in (0, 1))
        ):
            continue
        values.append(float(value))
        passed = score.get("passed")
        if type(passed) is bool:
            judged.append(passed)
            tried.append(passed)
    return values, judged, tried


def _share_of(rows):
    value = mean(rows)
    return {"value": value, "n": len(rows), "ci95": wilson_interval(value, len(rows))}


def per_try_aggregates(rows, run_id, key, binding, k):
    """A run's per-try aggregates of one metric, over the rows in view.

    Each row's tries become one value, and the run's number is the mean of
    its rows' values, like the mean of the assessed values:
    mode        the most common verdict of the row's scored tries (1 pass,
                0 fail; a tie is a fail)
    max         the best assessed value of the row's tries
    pass_at_k   1 when at least one of the row's tries passed
    pass_hat_k  1 when every one of the row's tries passed; each row is read
                against its own planned tries, so a row with a try that did
                not count (still running, not scored, unknown) is left out
    at_least:n  1 when at least n of the row's tries passed, n from 1 to k
    Errored and infrastructure-failed tries count as failed tries for
    pass_at_k, pass_hat_k and at_least (row_tries). A row none of whose tries
    counts is left out. An evaluation with no pass/fail rule has no verdicts:
    only max has a value. On a pass/fail evaluation an assessed value other
    than 0 or 1 is unknown for every one of them. `k` is the most tries a
    row of the run holds.
    Each entry is {value, n (rows counted), ci95}: a share carries its
    Wilson interval, the best value of a score its mean interval.
    """
    binding = binding or {}
    has_rule = pinned_pass_rule(binding)
    # The shape the interval of Best reads, and the type the verdicts read:
    # either makes its values pass/fail evidence, 0 or 1 only.
    pass_fail = (
        metric_shape(binding)["output_type"] == "pass_fail"
        or binding.get("output_type_normalized") in PASS_FAIL_OUTPUTS
    )
    best, mode, at_k, hat_k = [], [], [], []
    at_least = {n: [] for n in range(1, min(max(k, 1), AT_LEAST_MAX) + 1)}
    for row in rows:
        cell = row["results"].get(run_id)
        if cell is None:
            continue
        # The row's own planned tries, one record each.
        trials = cell.get("trials_detail") or []
        values, judged, tried = row_tries(trials, key, binary=pass_fail)
        if values:
            best.append(max(values))
        if not has_rule:
            continue
        if judged:
            passes = sum(judged)
            mode.append(1 if passes > len(judged) - passes else 0)
        if tried:
            passes = sum(tried)
            at_k.append(1 if passes else 0)
            if len(tried) == len(trials):
                hat_k.append(1 if passes == len(tried) else 0)
            for n, shares in at_least.items():
                shares.append(1 if passes >= n else 0)
    best_value = mean(best)
    return {
        "mode": _share_of(mode),
        "max": {
            "value": best_value,
            "n": len(best),
            "ci95": (
                wilson_interval(best_value, len(best))
                if pass_fail
                else mean_interval(best)
            ),
        },
        "pass_at_k": _share_of(at_k),
        "pass_hat_k": _share_of(hat_k),
        **{f"at_least:{n}": _share_of(shares) for n, shares in at_least.items()},
    }


def summary(selection):
    defs = metric_definitions(selection.runs, comparison=True)
    # How many tries a row of each run holds. The runs of one selection share
    # one plan, so every run in view holds the same number.
    tries = {
        str(run.id): tries_per_row(selection.trial_numbers[str(run.id)])
        for run in selection.runs
    }
    bindings = getattr(selection, "bindings", None) or {}
    aggregates = {}
    for definition in defs:
        key = definition["key"]
        # The page offers the per-try aggregates when a run in view plays
        # more than one try per row.
        definition["trials_k"] = max(
            (tries[rid] for rid in definition["runs"] if rid in tries), default=1
        )
        aggregates[key] = {}
        for run in selection.runs:
            rid = str(run.id)
            vals = [
                row["results"][rid]["scores"].get(key, {}).get("value")
                for row in selection.rows
            ]
            known = [v for v in vals if v is not None]
            aggregates[key][rid] = {
                "value": mean(known),
                "n": len(known),
                "rule": "assessed_example_mean",
                "total": len(vals),
                # The middle of the same row values (the
                # page's Median).
                "median": median(known),
                # Most common, Best, pass@k, pass^k and
                # at least n, each from the row's tries.
                "reducers": per_try_aggregates(
                    selection.rows, rid, key, bindings.get(rid, {}).get(key), tries[rid]
                ),
            }
    runs = []
    progress = progress_by_run(selection.runs, items=selection.items.values())
    for run in selection.runs:
        counts = {}
        for (rid, _, _), item in selection.items.items():
            if rid == str(run.id):
                counts[item.status] = counts.get(item.status, 0) + 1
        runs.append(project_run(run, counts=counts, progress=progress[str(run.id)]))
    return {
        "runs": runs,
        "metrics": defs,
        "human_metrics": [],
        "system_metrics": [],
        "aggregates": aggregates,
        "examples_total": len(selection.examples),
        "examples_in_view": len(selection.rows),
        "baseline_run_id": selection.baseline_run_id,
        # Rows worse, better and changed against the baseline (None
        # without a baseline and a run to compare with it).
        "show_counts": getattr(selection, "show_counts", None),
        "availability": {
            "human": "Not implemented in S1",
            "deltas": "Use analysis endpoint",
            "aggregation": "Assessed values only; unknowns are excluded, never zero-filled",
        },
    }


def vs_baseline_counts(selection, *, regressions=None):
    """Count shared recorded rows before paging; categorical values are pass rates."""
    baseline = selection.baseline_run_id
    if baseline is None:
        return None
    from model_hub.services.evaluator_comparison_identity import comparison_trial
    from model_hub.services.results_catalog import checked_runs

    runs = {str(run.id): run for run in selection.runs}
    candidates = [rid for rid in runs if rid != baseline]
    base_run = runs.get(baseline) or checked_runs(selection.dataset, [baseline])[0]
    recorded = {
        rid: {eid for run, eid, _ in selection.items if run == rid}
        for rid in [baseline, *candidates]
    }
    numbers = {}
    for eid, number in sorted(selection.plans[baseline]):
        numbers.setdefault(eid, []).append(number)
    pins = selection.bindings[baseline]
    counts = {
        key: {
            rid: dict.fromkeys(
                ("improved", "regressed", "unchanged", "not_comparable"), 0
            )
            for rid in candidates
        }
        for key in pins
    }
    for row in selection.rows:
        eid = row["example_id"]
        if eid not in recorded[baseline]:
            continue
        base = row["results"].get(baseline) or result_cell(
            [
                comparison_trial(
                    selection.items.get((baseline, eid, n)), n, base_run, pins
                )
                for n in numbers[eid]
            ]
        )
        for key, pin in pins.items():
            shape = metric_shape(pin)
            categorical = shape["output_type"] in {"choice", "pass_fail"}
            direction = True if categorical else shape["higher_is_better"]
            for rid in candidates:
                if eid not in recorded[rid]:
                    continue
                values = []
                for cell in (base, row["results"][rid]):
                    score = cell["scores"].get(key, {})
                    value = (
                        mean(score.get("trials", []))
                        if categorical
                        else score.get("value")
                    )
                    values.append(value if score.get("status") == "assessed" else None)
                bv, cv = values
                state = "not_comparable"
                if bv is not None and cv is not None and direction is not None:
                    state = (
                        "unchanged"
                        if cv == bv
                        else ("improved" if (cv > bv) == direction else "regressed")
                    )
                counts[key][rid][state] += 1
                if state == "regressed" and regressions is not None:
                    regressions.append(
                        {
                            "example_id": eid,
                            "metric_id": key,
                            "run_id": rid,
                            "baseline_score": bv,
                            "score": cv,
                        }
                    )
    return counts


def response_parts(selection, query):
    total = len(selection.rows)
    page, size = query.get("page", 0), query.get("page_size", 1000)
    start = page * size
    more = start + size < total
    return {
        "total": total,
        "summary": summary(selection),
        "vs_baseline": vs_baseline_counts(selection),
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "has_more": more,
            "next": page + 1 if more else None,
        },
        "scope": {
            "population": total,
            "complete": True,
            "selection_id": selection.selection_id,
        },
    }


def bound_response(payload):
    if len(JSONRenderer().render(payload)) > MAX_RESPONSE_BYTES:
        raise ResultsReadError("Response exceeds 8 MiB; reduce page_size or select fewer runs.", 503)
    return payload


def grid_results(selection, query):
    payload = response_parts(selection, query)
    rows = selection.rows
    if query.get("full"):
        wanted = str(query["example_id"])
        position = next((i for i, r in enumerate(rows) if r["example_id"] == wanted), None)
        if position is None:
            raise ResultsReadError("Example not found in this selection", 404)
        row = {**rows[position], "neighbors": {
            "prev": rows[position - 1]["example_id"] if position else None,
            "next": rows[position + 1]["example_id"] if position + 1 < len(rows) else None,
            "position": position + 1, "total": len(rows)}}
        payload["rows"] = [row]
        payload["pagination"].update(page=0, page_size=1, has_more=False, next=None)
    else:
        start = query.get("page", 0) * query.get("page_size", 1000)
        page_rows = rows[start:start + query.get("page_size", 1000)]
        if query.get("ids_only"):
            payload["example_ids"] = [r["example_id"] for r in page_rows]
        else:
            output_rows = []
            for row in page_rows:
                cells = {}
                for rid, original in row["results"].items():
                    cell = {k: v for k, v in original.items() if k != "trials_detail"}
                    # Match the row Runs trace contract without repeating large
                    # outputs or evaluator explanations in a paginated grid.
                    cell["trials_detail"] = [
                        {
                            key: trial[key]
                            for key in ("trial_number", "status", "trace_id")
                        }
                        for trial in original.get("trials_detail", [])
                    ]
                    cell["output"], cell["output_truncated"], cell["output_bytes"] = _bounded(cell["output"], 4096)
                    cells[rid] = cell
                output_rows.append({**row, "results": cells})
            payload["rows"] = output_rows
    return bound_response(payload)
