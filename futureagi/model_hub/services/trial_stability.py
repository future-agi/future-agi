"""Derived per-example repeated-trial distributions over existing run evidence."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import fsum, isfinite
from statistics import pstdev
from typing import Any

from model_hub.services.experiment_analysis import (
    RunItemRecord,
    _index_items,
    _normalise_item,
    _score_result,
)


@dataclass(frozen=True)
class TrialAggregate:
    """Observed score distribution for one example across its trials.

    ``score_stddev`` is the population standard deviation of finite assessed
    scores. It is ``None`` when fewer than two assessed trials exist; it is a
    descriptive stability signal, not a confidence interval.
    """

    recorded_trial_count: int
    missing_trial_count: int
    assessed_trial_count: int
    score_average: float | None
    score_min: float | None
    score_max: float | None
    score_stddev: float | None


@dataclass(frozen=True)
class TrialDeltaAggregate:
    """Distribution of candidate-minus-baseline scores for paired trials."""

    paired_trial_count: int
    delta_average: float | None
    delta_stddev: float | None


@dataclass(frozen=True)
class TrialStability:
    """Per-example repeatability projection over the selected trial identities."""

    example_id: str
    expected_trial_count: int
    baseline: TrialAggregate
    candidate: TrialAggregate
    paired: TrialDeltaAggregate


def _distribution(values: list[float]) -> tuple[float | None, float | None]:
    """Return finite mean/stddev without overflowing intermediate sums."""

    if not values:
        return None, None
    scale = max(abs(value) for value in values)
    if not scale:
        return 0.0, 0.0 if len(values) > 1 else None
    normalized = [value / scale for value in values]
    mean = fsum(normalized) / len(normalized)
    average = mean * scale
    if len(values) < 2:
        return average, None
    standard_deviation = pstdev(values)
    return (
        average if isfinite(average) else None,
        standard_deviation if isfinite(standard_deviation) else None,
    )


def _trial_aggregate(
    groups: Iterable[list[RunItemRecord]], metric: str, expected_count: int
) -> TrialAggregate:
    groups = list(groups)
    values = [
        score.value
        for group in groups
        if len(group) == 1
        for score in (_score_result(group[0], metric),)
        if score.value is not None
    ]
    average, standard_deviation = _distribution(values)
    return TrialAggregate(
        recorded_trial_count=len(groups),
        missing_trial_count=expected_count - len(groups),
        assessed_trial_count=len(values),
        score_average=average,
        score_min=min(values) if values else None,
        score_max=max(values) if values else None,
        score_stddev=standard_deviation,
    )


def summarize_trial_stability(
    baseline_items: Iterable[RunItemRecord | Mapping[str, Any]],
    candidate_items: Iterable[RunItemRecord | Mapping[str, Any]],
    *,
    expected_identities: Iterable[tuple[str, int]],
    metric: str = "score",
) -> tuple[TrialStability, ...]:
    """Describe each example's observed repeated trials, including missing ones.

    Both runs must use the caller's exact plan. Duplicated identities and
    unassessed evidence cannot contribute scores or paired deltas. Population
    deviations describe only observed assessed trials; no significance or
    independence claim is made. Score direction does not change raw deltas.
    """
    baseline, candidate = _index_items(baseline_items), _index_items(candidate_items)
    plan = [
        _normalise_item(RunItemRecord(example_id, trial_number=trial))
        for example_id, trial in expected_identities
    ]
    identities = {(item.example_id, item.trial_number) for item in plan}
    if len(identities) != len(plan):
        raise ValueError("expected trial identities must be unique")
    if (baseline.keys() | candidate.keys()) - identities:
        raise ValueError("recorded trial is outside the expected plan")
    by_example: dict[str, list[tuple[str, int]]] = {}
    for example_id, trial_number in sorted(identities):
        by_example.setdefault(example_id, []).append((example_id, trial_number))
    result: list[TrialStability] = []
    for example_id in sorted(by_example):
        selected = by_example[example_id]
        baseline_groups = [baseline[key] for key in selected if key in baseline]
        candidate_groups = [candidate[key] for key in selected if key in candidate]
        deltas = []
        for key in selected:
            before, after = baseline.get(key, []), candidate.get(key, [])
            if len(before) != 1 or len(after) != 1:
                continue
            before_score = _score_result(before[0], metric).value
            after_score = _score_result(after[0], metric).value
            if before_score is None or after_score is None:
                continue
            delta = after_score - before_score
            if isfinite(delta):
                deltas.append(delta)
        delta_average, delta_stddev = _distribution(deltas)
        result.append(
            TrialStability(
                example_id=example_id,
                expected_trial_count=len(selected),
                baseline=_trial_aggregate(baseline_groups, metric, len(selected)),
                candidate=_trial_aggregate(candidate_groups, metric, len(selected)),
                paired=TrialDeltaAggregate(
                    paired_trial_count=len(deltas),
                    delta_average=delta_average,
                    delta_stddev=delta_stddev,
                ),
            )
        )
    return tuple(result)
