"""Bounded, read-only comparison of compatible persisted experiment runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import TypeAlias
from uuid import UUID

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_analysis import (
    ASSESSED,
    INVALID,
    NOT_ASSESSED,
    SCORE_STATUSES,
    ComparisonSummary,
    RunAggregate,
    RunItemRecord,
    compare_run_items,
)
from model_hub.services.experiment_run_writes import (
    RunWriteError,
    _canonical,
    _planned,
    _scope,
    _uuid,
)
from tfc.routers import primary_required

_MAX_ITEMS = 10_000
ItemIdentity: TypeAlias = tuple[str, int]


class RunComparisonError(RunWriteError):
    """The runs cannot be compared within the requested scope and contract."""


@dataclass(frozen=True)
class RunCounts:
    expected: int
    recorded: int
    missing: int
    completed: int
    failed: int
    assessed: int


@dataclass(frozen=True)
class TraceReference:
    run_item_id: str
    trace_id: str | None
    root_span_id: str | None


@dataclass(frozen=True)
class CaseTraceReferences:
    example_id: str
    trial_number: int
    baseline: TraceReference | None
    candidate: TraceReference | None


@dataclass(frozen=True)
class PersistedRunComparison:
    baseline_run_id: str
    candidate_run_id: str
    dataset_version_id: str
    metric_id: str
    evaluator_version_id: str
    comparison: ComparisonSummary
    baseline_counts: RunCounts
    candidate_counts: RunCounts
    missing_by_side: Mapping[str, tuple[ItemIdentity, ...]]
    trace_refs: tuple[CaseTraceReferences, ...]


def _load_run(
    run_id: UUID | str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None,
) -> ExperimentRun:
    try:
        return ExperimentRun.no_workspace_objects.get(
            id=_uuid(run_id), **_scope(organization_id, workspace_id)
        )
    except ExperimentRun.DoesNotExist as exc:
        raise RunComparisonError("experiment run was not found in this tenant") from exc


def _plan(run: ExperimentRun) -> set[ItemIdentity]:
    metadata = run.metadata
    envelope = (
        metadata.get("_futureagi_run_write") if isinstance(metadata, dict) else None
    )
    plan = envelope.get("plan") if isinstance(envelope, dict) else None
    identities = plan.get("identities") if isinstance(plan, dict) else None
    if not isinstance(identities, list) or len(identities) > _MAX_ITEMS:
        raise RunComparisonError(
            f"run plan must contain at most {_MAX_ITEMS} identities"
        )
    return _planned(run)


def _bindings(run: ExperimentRun) -> dict[str, dict]:
    if not isinstance(run.evaluator_versions, list):
        raise RunComparisonError("evaluator bindings must be a list")
    bindings = {}
    for binding in run.evaluator_versions:
        if not isinstance(binding, dict):
            raise RunComparisonError(
                "evaluator binding must contain a metric and version"
            )
        metric_id = _uuid(binding.get("metric_id"))
        _uuid(binding.get("evaluator_version_id"))
        _canonical(binding)
        if metric_id in bindings:
            raise RunComparisonError("evaluator metric binding is duplicated")
        bindings[metric_id] = binding
    return bindings


def _score(refs: object, bindings: dict[str, dict], metric_id: str) -> dict:
    """Refuse ambiguous evidence, including invalid unrelated references."""
    invalid = {"status": INVALID}
    if not isinstance(refs, list):
        return invalid
    selected = {"status": NOT_ASSESSED}
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict):
            return invalid
        try:
            ref_metric = _uuid(ref.get("metric_id"))
            ref_version = _uuid(ref.get("evaluator_version_id"))
            _canonical(ref)
        except RunWriteError:
            return invalid
        binding = bindings.get(ref_metric)
        if (
            ref_metric in seen
            or binding is None
            or ref_version != _uuid(binding["evaluator_version_id"])
        ):
            return invalid
        seen.add(ref_metric)
        status = ref.get("status")
        if not isinstance(status, str) or status not in SCORE_STATUSES:
            return invalid
        value = ref.get("value")
        if status == ASSESSED:
            try:
                valid = type(value) in (int, float) and isfinite(float(value))
            except OverflowError:
                valid = False
            if not valid:
                return invalid
        if ref_metric == metric_id:
            selected = {"status": status, "value": value}
    return selected


def _items(run: ExperimentRun, plan: set[ItemIdentity]) -> list[ExperimentRunItem]:
    items = list(
        ExperimentRunItem.no_workspace_objects.filter(run=run, deleted=False)
        .only(
            "id",
            "run_id",
            "example_id",
            "trial_number",
            "status",
            "error",
            "score_refs",
            "trace_id",
            "root_span_id",
        )
        .order_by("example_id", "trial_number")[: _MAX_ITEMS + 1]
    )
    if len(items) > _MAX_ITEMS:
        raise RunComparisonError(f"run has more than {_MAX_ITEMS} recorded items")
    if any((str(item.example_id), item.trial_number) not in plan for item in items):
        raise RunComparisonError("recorded item is outside the pinned run plan")
    return items


def _records(
    items: list[ExperimentRunItem], bindings: dict[str, dict], metric_id: str
) -> list[RunItemRecord]:
    return [
        RunItemRecord(
            example_id=item.example_id,
            trial_number=item.trial_number,
            # A legacy completed row carrying a task error is not a success.
            status="failed"
            if item.status == "completed" and item.error is not None
            else item.status,
            scores={metric_id: _score(item.score_refs, bindings, metric_id)},
        )
        for item in items
    ]


def _counts(expected: int, summary: RunAggregate) -> RunCounts:
    return RunCounts(
        expected=expected,
        recorded=summary.item_count,
        missing=expected - summary.item_count,
        completed=summary.completed_count,
        failed=summary.failed_count,
        assessed=summary.score_count,
    )


def _traces(items: list[ExperimentRunItem]) -> dict[ItemIdentity, TraceReference]:
    return {
        (str(item.example_id), item.trial_number): TraceReference(
            str(item.id), item.trace_id, item.root_span_id
        )
        for item in items
    }


@primary_required
def compare_experiment_runs(
    *,
    baseline_run_id: UUID | str,
    candidate_run_id: UUID | str,
    organization_id: UUID | str,
    metric_id: UUID | str,
    workspace_id: UUID | str | None = None,
    passing_score: float | None = None,
    higher_is_better: bool = True,
) -> PersistedRunComparison:
    """Compare exact version/plan/evaluator pins; no inferred alignment or CI verdict.

    The caller supplies an authenticated tenant scope and any required workspace
    restriction. Reads use primary and are bounded at 10,000 planned/recorded
    items per run. Only real items feed analysis; both-absent cases remain in
    ``missing_by_side`` and trace references without inflating execution counts.
    Run headers may be incomplete: counts describe the evidence read here.
    """
    try:
        metric_id = _uuid(metric_id)
        baseline = _load_run(baseline_run_id, organization_id, workspace_id)
        candidate = _load_run(candidate_run_id, organization_id, workspace_id)
        if baseline.dataset_version_id != candidate.dataset_version_id:
            raise RunComparisonError("runs must share the same dataset version")
        plan = _plan(baseline)
        if plan != _plan(candidate):
            raise RunComparisonError("runs must share the exact pinned item plan")
        baseline_bindings, candidate_bindings = (
            _bindings(baseline),
            _bindings(candidate),
        )
        before, after = (
            baseline_bindings.get(metric_id),
            candidate_bindings.get(metric_id),
        )
        if before is None or after is None:
            raise RunComparisonError("selected metric is not pinned in both runs")
        if _canonical(before) != _canonical(after):
            raise RunComparisonError("selected evaluator binding snapshots must match")
        baseline_items, candidate_items = (
            _items(baseline, plan),
            _items(candidate, plan),
        )
    except RunWriteError as exc:
        raise RunComparisonError(str(exc)) from exc

    comparison = compare_run_items(
        _records(baseline_items, baseline_bindings, metric_id),
        _records(candidate_items, candidate_bindings, metric_id),
        metric=metric_id,
        passing_score=passing_score,
        higher_is_better=higher_is_better,
    )
    baseline_traces, candidate_traces = (
        _traces(baseline_items),
        _traces(candidate_items),
    )
    return PersistedRunComparison(
        baseline_run_id=str(baseline.id),
        candidate_run_id=str(candidate.id),
        dataset_version_id=str(baseline.dataset_version_id),
        metric_id=metric_id,
        evaluator_version_id=_uuid(before["evaluator_version_id"]),
        comparison=comparison,
        baseline_counts=_counts(len(plan), comparison.baseline),
        candidate_counts=_counts(len(plan), comparison.candidate),
        missing_by_side={
            "baseline": tuple(sorted(plan - baseline_traces.keys())),
            "candidate": tuple(sorted(plan - candidate_traces.keys())),
        },
        trace_refs=tuple(
            CaseTraceReferences(
                example_id,
                trial,
                baseline_traces.get((example_id, trial)),
                candidate_traces.get((example_id, trial)),
            )
            for example_id, trial in sorted(plan)
        ),
    )
