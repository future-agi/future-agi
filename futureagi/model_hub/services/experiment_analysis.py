"""Storage-free aggregates and comparisons over experiment example/trial results.

Callers supply records after resolving score references and dataset metadata.
No database records, API routes, or statistical-analysis storage are added here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from math import fsum, isfinite
from numbers import Real
from typing import Any
from uuid import UUID

ASSESSED = "assessed"
UNKNOWN = "unknown"
NOT_ASSESSED = "not_assessed"
UNSUPPORTED = "unsupported"
ERROR = "error"
IMPROVED = "improved"
REGRESSED = "regressed"
TIE = "tie"
MISSING = "missing"
DUPLICATE = "duplicate"
INVALID = "invalid"

SCORE_STATUSES = frozenset({ASSESSED, UNKNOWN, NOT_ASSESSED, UNSUPPORTED, ERROR})
COMPLETED_STATUSES = frozenset({"completed", "success", "succeeded"})
FAILED_STATUSES = frozenset({"failed", "error"})
UNFINISHED_STATUSES = frozenset(
    {"pending", "running", "queued", "draft", "partial", "cancelled", "skipped"}
)


@dataclass(frozen=True)
class RunItemRecord:
    """Adapter-facing result. Named scores accept scalars or evidence envelopes."""

    example_id: str | UUID | None
    status: str = "completed"
    score: float | int | None = None
    scores: Mapping[str, Any] = field(default_factory=dict)
    split_names: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    trial_number: int = 1


@dataclass(frozen=True)
class AnalysisFilter:
    """Exact conditions; one member of a comparison pair must match all of them."""

    split_name: str | None = None
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)
    item_statuses: frozenset[str] | None = None


@dataclass(frozen=True)
class ScoreResult:
    value: float | None
    status: str


@dataclass(frozen=True)
class RunAggregate:
    """Item counts include duplicates; score aggregates exclude ambiguous identities."""

    item_count: int
    completed_count: int
    failed_count: int
    score_count: int
    score_average: float | None
    score_min: float | None
    score_max: float | None
    pass_count: int | None
    pass_rate: float | None
    status_counts: Mapping[str, int]
    score_status_counts: Mapping[str, int]


@dataclass(frozen=True)
class CaseComparison:
    """One pair aligned by (example_id, trial_number), including missing sides."""

    example_id: str
    status: str
    baseline_status: str | None
    candidate_status: str | None
    baseline_score: float | None = None
    candidate_score: float | None = None
    score_delta: float | None = None
    missing_side: str | None = None
    trial_number: int = 1


@dataclass(frozen=True)
class ComparisonSummary:
    baseline: RunAggregate
    candidate: RunAggregate
    cases: tuple[CaseComparison, ...]
    status_counts: Mapping[str, int]

    @property
    def comparable_count(self) -> int:
        return sum(
            case.status in {IMPROVED, REGRESSED, TIE}
            and case.baseline_score is not None
            and case.candidate_score is not None
            for case in self.cases
        )


def _normalise_item(item: RunItemRecord | Mapping[str, Any]) -> RunItemRecord:
    if isinstance(item, Mapping):
        scores = item.get("scores", {})
        metadata = item.get("metadata", {})
        split_names = item.get("split_names", ())
        item = RunItemRecord(
            example_id=item.get("example_id"),
            status=item.get("status", "completed"),
            score=item.get("score"),
            scores={} if scores is None else scores,
            split_names=() if split_names is None else split_names,
            metadata={} if metadata is None else metadata,
            error=item.get("error"),
            trial_number=item.get("trial_number", 1),
        )
    if not isinstance(item.example_id, (str, UUID)) or not str(item.example_id).strip():
        raise ValueError("example_id must be a non-empty string or UUID")
    if type(item.trial_number) is not int or item.trial_number < 1:
        raise ValueError("trial_number must be a positive integer")
    if not isinstance(item.metadata, Mapping) or not isinstance(item.scores, Mapping):
        raise ValueError("metadata and scores must be mappings")
    if not isinstance(item.status, str):
        raise ValueError("item status must be a string")
    splits = item.split_names
    if isinstance(splits, str):
        splits = (splits,)
    if not isinstance(splits, (tuple, list)) or any(
        not isinstance(split, str) for split in splits
    ):
        raise ValueError("split_names must contain strings")
    return replace(item, example_id=str(item.example_id), split_names=tuple(splits))


def _matches(item: RunItemRecord, filters: AnalysisFilter | None) -> bool:
    return filters is None or (
        (filters.split_name is None or filters.split_name in item.split_names)
        and (filters.item_statuses is None or item.status in filters.item_statuses)
        and all(
            key in item.metadata and item.metadata[key] == value
            for key, value in filters.metadata_equals.items()
        )
    )


def _index_items(
    items: Iterable[RunItemRecord | Mapping[str, Any]],
) -> dict[tuple[str, int], list[RunItemRecord]]:
    grouped: dict[tuple[str, int], list[RunItemRecord]] = {}
    for raw in items:
        item = _normalise_item(raw)
        grouped.setdefault((item.example_id, item.trial_number), []).append(item)
    return grouped


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        numeric = float(value)
    except OverflowError:
        return None
    return numeric if isfinite(numeric) else None


def _score_result(item: RunItemRecord, metric: str) -> ScoreResult:
    if item.status in FAILED_STATUSES:
        return ScoreResult(None, ERROR)
    if item.status in UNFINISHED_STATUSES:
        return ScoreResult(None, NOT_ASSESSED)
    if item.status not in COMPLETED_STATUSES:
        return ScoreResult(None, INVALID)

    raw = (
        item.score
        if metric == "score" and item.score is not None
        else item.scores.get(metric)
    )
    if raw is None:
        return ScoreResult(None, NOT_ASSESSED)
    if isinstance(raw, Mapping):
        value = raw.get("value", raw.get("score"))
        status = raw.get("status", ASSESSED if value is not None else NOT_ASSESSED)
        if not isinstance(status, str) or status not in SCORE_STATUSES:
            return ScoreResult(None, INVALID)
        if status != ASSESSED:
            return ScoreResult(None, status)
        raw = value
    numeric = _finite_number(raw)
    return ScoreResult(numeric, ASSESSED if numeric is not None else INVALID)


def _validate_options(passing_score: float | None, higher_is_better: bool) -> None:
    if passing_score is not None and _finite_number(passing_score) is None:
        raise ValueError("passing_score must be finite and numeric")
    if not isinstance(higher_is_better, bool):
        raise ValueError("higher_is_better must be a boolean")


def _aggregate(
    groups: Iterable[list[RunItemRecord]],
    metric: str,
    passing_score: float | None,
    higher_is_better: bool,
) -> RunAggregate:
    statuses: Counter[str] = Counter()
    score_statuses: Counter[str] = Counter()
    values: list[float] = []
    for group in groups:
        for item in group:
            statuses[item.status] += 1
            score = (
                ScoreResult(None, DUPLICATE)
                if len(group) > 1
                else _score_result(item, metric)
            )
            score_statuses[score.status] += 1
            if score.value is not None:
                values.append(score.value)
    count = len(values)
    # Scale first: finite inputs must not overflow the mean's intermediate sum.
    scale = max((abs(value) for value in values), default=0.0)
    average = (
        (fsum(value / scale for value in values) / count * scale)
        if scale
        else (0.0 if count else None)
    )
    passes = (
        None
        if passing_score is None
        else sum(
            value >= passing_score if higher_is_better else value <= passing_score
            for value in values
        )
    )
    return RunAggregate(
        item_count=sum(statuses.values()),
        completed_count=sum(statuses[status] for status in COMPLETED_STATUSES),
        failed_count=sum(statuses[status] for status in FAILED_STATUSES),
        score_count=count,
        score_average=average,
        score_min=min(values) if values else None,
        score_max=max(values) if values else None,
        pass_count=passes,
        pass_rate=passes / count if passes is not None and count else None,
        status_counts=dict(sorted(statuses.items())),
        score_status_counts=dict(sorted(score_statuses.items())),
    )


def summarize_run_items(
    items: Iterable[RunItemRecord | Mapping[str, Any]],
    *,
    metric: str = "score",
    filters: AnalysisFilter | None = None,
    passing_score: float | None = None,
    higher_is_better: bool = True,
) -> RunAggregate:
    """Summarize selected example/trial identities; invalid identity raises ValueError.

    A filter selects an entire identity, preserving duplicate detection. Score
    states remain explicit. Pass rates use assessed scores as the denominator;
    without an explicit threshold, pass count and rate are None.
    """

    _validate_options(passing_score, higher_is_better)
    groups = _index_items(items)
    selected = (
        group
        for group in groups.values()
        if any(_matches(item, filters) for item in group)
    )
    return _aggregate(selected, metric, passing_score, higher_is_better)


def _compare_case(
    identity: tuple[str, int],
    baseline: list[RunItemRecord],
    candidate: list[RunItemRecord],
    metric: str,
    higher_is_better: bool,
) -> CaseComparison:
    common = {
        "example_id": identity[0],
        "trial_number": identity[1],
        "baseline_status": baseline[0].status if baseline else None,
        "candidate_status": candidate[0].status if candidate else None,
    }
    if len(baseline) > 1 or len(candidate) > 1:
        return CaseComparison(**common, status=DUPLICATE)
    if not baseline or not candidate:
        return CaseComparison(
            **common,
            status=MISSING,
            missing_side="baseline" if not baseline else "candidate",
        )
    before = _score_result(baseline[0], metric)
    after = _score_result(candidate[0], metric)
    common.update(baseline_status=before.status, candidate_status=after.status)
    delta = None
    if before.value is None or after.value is None:
        status = next(
            status
            for status in (INVALID, ERROR, UNKNOWN, UNSUPPORTED, NOT_ASSESSED)
            if status in {before.status, after.status}
        )
    else:
        delta = after.value - before.value
        if not isfinite(delta):
            status, delta = INVALID, None
        elif delta == 0:
            status = TIE
        else:
            status = IMPROVED if (delta > 0) == higher_is_better else REGRESSED
    return CaseComparison(
        **common,
        status=status,
        baseline_score=before.value,
        candidate_score=after.value,
        score_delta=delta,
    )


def compare_run_items(
    baseline_items: Iterable[RunItemRecord | Mapping[str, Any]],
    candidate_items: Iterable[RunItemRecord | Mapping[str, Any]],
    *,
    metric: str = "score",
    filters: AnalysisFilter | None = None,
    passing_score: float | None = None,
    higher_is_better: bool = True,
) -> ComparisonSummary:
    """Compare by (example_id, trial_number), then filter the aligned pairs.

    A pair is selected when either side matches every filter; both sides remain
    in the comparison and its aggregates. Duplicate identities stay explicit.
    ``score_delta`` is candidate minus baseline; set ``higher_is_better=False``
    for error, cost, or other metrics where smaller scores are improvements.
    """

    _validate_options(passing_score, higher_is_better)
    baseline = _index_items(baseline_items)
    candidate = _index_items(candidate_items)
    identities = [
        key
        for key in sorted(baseline.keys() | candidate.keys())
        if any(
            _matches(item, filters)
            for item in baseline.get(key, []) + candidate.get(key, [])
        )
    ]
    cases = tuple(
        _compare_case(
            key, baseline.get(key, []), candidate.get(key, []), metric, higher_is_better
        )
        for key in identities
    )
    return ComparisonSummary(
        baseline=_aggregate(
            (baseline[key] for key in identities if key in baseline),
            metric,
            passing_score,
            higher_is_better,
        ),
        candidate=_aggregate(
            (candidate[key] for key in identities if key in candidate),
            metric,
            passing_score,
            higher_is_better,
        ),
        cases=cases,
        status_counts=dict(sorted(Counter(case.status for case in cases).items())),
    )
