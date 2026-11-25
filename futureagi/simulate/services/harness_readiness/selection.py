"""Which selected cases a pairing's readiness holds back, and why.

Each case lands in exactly one bucket, first match: blocked, stale, untested, then
eligible. A row holds a case only when the case needs it; a tool row whose targets
name none of the case's tools reads Supported for it, and an unmeasured row holds no
case. While the pairing is being checked, every case reads untested: the run gate
refuses it and the check has not answered yet.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict, cast
from uuid import UUID

from simulate.models import (
    HarnessAssembledBundle,
    HarnessSuiteMember,
    HostedHarnessJob,
)
from simulate.selectors.runs import validation_jobs
from simulate.services.canonical_json import canonical_digest
from simulate.services.harness_readiness.catalogue import (
    CATALOGUE,
    GATE_OPEN,
    AnswerView,
    AreaView,
    Pairing,
    PairingView,
    RowView,
    Status,
    VersionRef,
    WorkerBoundaryView,
)
from simulate.services.harness_readiness.evidence import (
    Evidence,
    pair_bundle,
    read_evidence,
)
from simulate.services.harness_readiness.report import (
    ReadinessReport,
    evidence_revision,
    readiness_report,
)
from simulate.services.harness_readiness.rules import answer
from simulate.services.harness_readiness.workers import (
    NO_HOLD,
    CaseHold,
    blocked_count,
    bypassed_workers,
    case_hold,
    case_holds,
)
from simulate.services.harness_scenario_lifecycle import (
    UNREAD_REASONS,
    reason_text,
    runnable,
    scenario_lifecycle,
)
from simulate.services.harness_scenario_needs import (
    ACTING,
    TOOL_ROWS,
    CaseNeeds,
    case_needs,
)
from simulate.services.harness_starting_records import scenario_token
from simulate.services.harness_versioning import validation
from simulate.services.harness_versioning.read import version_refs
from simulate.services.hosted_harness import HostedHarnessError

Bucket = Literal["eligible", "blocked", "stale", "untested"]
BUCKETS: tuple[Bucket, ...] = ("eligible", "blocked", "stale", "untested")
LIST_LIMIT = 200
PAGE_SIZE = 50
DROPPED_REASON = "dropped when these versions were checked"
# Held-back key for a selected case certifying the pair dropped.
DROPPED_ROW = "scenario_dropped"
# Held-back keys for a case not validated on the run's pair, and an archived case.
NOT_VALIDATED_ROW = "not_validated"
ARCHIVED_ROW = "scenario_archived"
# Held-back key for a case validated on versions not saved yet: only an active suite's cases run.
NOT_ACTIVE_ROW = "not_active"
# Held-back key for a case its last proof found a defect in (a leaked answer, a control that passed, no control): a
# validation would only repeat the verdict, so it waits for the case to be fixed.
PROOF_FAILED_ROW = "proof_failed"
# Held-back key for a case something it needs could not be read for (its seed records, its cited files, its pair's
# last proofs): nothing failed, it is unknown.
SEED_UNREAD_ROW = "unreadable"
# Held-back key for a case whose reference calls a tool the pairing's agent version does not have.
TOOL_MISSING_ROW = "tool_not_on_agent"
# The Draft reasons a validation can settle; any other Draft reason is a proof's verdict on the case itself.
UNVALIDATED_REASONS = frozenset(
    {
        "not_validated",
        "validation_incomplete",
        "validation_failed",
        "validation_ended_on_our_side",
        "proved_on_other_agent",
        "tools_unread",
    }
)
LIFECYCLE_LABELS = {
    NOT_VALIDATED_ROW: "Validation",
    PROOF_FAILED_ROW: "Proof",
    ARCHIVED_ROW: "Archived",
    NOT_ACTIVE_ROW: "Not saved",
    SEED_UNREAD_ROW: "Starting records",
    TOOL_MISSING_ROW: "Agent tools",
}
# A case whose actors act in the world or whose faults are set on a tool the world's door does not stand in front of.
NO_DOOR_ROW = "no_world_door"
NO_DOOR_REASON = (
    "Actors cannot act and faults cannot be set here: this agent's tools run inside it. Serve the tools from a "
    "tools service to test this."
)
UNGRADABLE_REASON = "has no check this environment version grades"
# Untested key for a case whose reliance on worker limits ALK sealed no answer for.
WORKER_LIMITS_UNKNOWN = "worker_limits_not_known"
# Held-back key for a case ALK holds on worker limits without naming a worker.
WORKER_LIMITS_HELD = "worker_limits"
_HOLDING = frozenset({"blocked", "unavailable", "stale", "untested"})
_EVIDENCE_ROWS = frozenset(one.id for one in CATALOGUE if one.area == "evidence")
_MEASURED = tuple(one.id for one in CATALOGUE if one.measured)


class CaseList(TypedDict):
    keys: list[str]
    truncated: bool


class DroppedCase(TypedDict):
    key: str
    code: Literal["scenario_dropped", "scenario_ungradable"]
    reason: str


class NotGradable(TypedDict):
    key: str
    reason: Literal[
        "check_unreadable", "no_checks", "evidence_not_supported", "judge_unavailable"
    ]


class Grading(TypedDict):
    gradable: int
    not_gradable: list[NotGradable]
    truncated: bool


class Exclusions(CaseList):
    count: int
    # A suite case not selected for this run.
    reason: Literal["not_selected"]


class ChangedSince(TypedDict):
    run_id: str
    suite_version: VersionRef | None


class Changes(CaseList):
    count: int
    # The newest run of this agent and environment version the selected cases are
    # compared with; ``None`` when there is none.
    since: ChangedSince | None


class Scope(TypedDict):
    suite_total: int
    excluded: Exclusions
    changed: Changes


class HeldBy(TypedDict):
    """A blocked row or declared worker holding selected cases back, named for the run dialog."""

    id: str
    name: str
    area: str | None
    count: int
    reason: str | None
    # What the run dialog shows for it, e.g. "Refund worker's limits are not held".
    text: str


class SelectionView(Scope):
    requested: int
    eligible: int
    blocked: int
    stale: int
    untested: int
    lists: dict[Bucket, CaseList]
    held_by: list[HeldBy]
    # The first area, in report order, holding selected cases back; null when none is blocked.
    blocking_area: str | None
    dropped: list[DroppedCase]
    grading: Grading
    requirements_recorded: bool
    # What a run does when every selected case is held back only for not being validated on the pairing.
    next: validation.UnprovedNext | None
    # What a run of the selection sends, and what it holds back and why.
    running: int
    held_back: HeldBack
    reasons: dict[str, str]


class Affected(TypedDict):
    count: int
    of: int
    keys: list[str]
    truncated: bool


class SelectionRowView(RowView):
    needed_by: int
    affected: Affected


class SelectionReport(TypedDict):
    pairing: PairingView
    validation: validation.ValidationView
    answer: AnswerView
    areas: list[AreaView]
    rows: list[SelectionRowView]
    workers: list[WorkerBoundaryView]
    counts: dict[Status, int]
    blockers: int
    catalog_version: str
    rules_version: str
    computed_at: str
    selection: SelectionView


class CaseItem(TypedDict):
    key: str
    number: int
    name: str
    persona: str
    by: list[str]


class CasePage(TypedDict):
    items: list[CaseItem]
    next: str | None


@dataclass(frozen=True)
class Placed:
    """A requested case, its bucket and what put it there (row ids, or
    ``pair_validating`` / ``pair_failed`` / ``not_validated`` / ``scenario_archived`` / ``unknown_need``),
    and the rows holding it."""

    case: CaseNeeds
    bucket: Bucket
    by: list[str]
    held_by: frozenset[str]
    needed: frozenset[str]
    gradable: NotGradable | None
    # ALK's sealed hold for the case's worker limits.
    hold: CaseHold
    # The case's lifecycle reason, when its lifecycle holds it back.
    lifecycle_reason: str | None = None


@dataclass(frozen=True)
class _Placement:
    placed: list[Placed]
    dropped: list[DroppedCase]
    recorded: bool
    # Every case of the suite version, in number order.
    suite: list[str]


def selection_report(
    pairing: Pairing, *, keys: list[str] | None, trials: int
) -> SelectionReport:
    """The pairing's readiness with the selected cases bucketed; ``keys`` ``None``
    selects the whole suite. No writes.

    Raises:
        HostedHarnessError: 400 ``scenario_selection_empty``,
            ``scenario_selection_duplicate`` or ``scenario_selection_unknown``.
    """
    bundle = pair_bundle(pairing)
    evidence = read_evidence(pairing, bundle)
    report = readiness_report(pairing, bundle=bundle, evidence=evidence)
    placement = _place_all(
        pairing,
        report["rows"],
        keys=keys,
        trials=trials,
        bundle=bundle,
        evidence=evidence,
        workers=report["workers"],
    )
    placed = placement.placed
    requested = len(placed)
    held_back, held_counts, held_reasons = _held_back(
        placement, report["rows"], report["workers"]
    )
    by_bucket: dict[Bucket, list[str]] = {bucket: [] for bucket in BUCKETS}
    for one in placed:
        by_bucket[one.bucket].append(one.case.key)
    not_gradable = [one.gradable for one in placed if one.gradable is not None]
    rows: list[SelectionRowView] = []
    for row in report["rows"]:
        held = _row_holds(row, placed)
        rows.append(
            {
                **row,
                "needed_by": sum(1 for one in placed if row["id"] in one.needed),
                "affected": {"count": len(held), "of": requested, **_capped(held)},
            }
        )
    held_by = _held_by(rows, report["workers"], placed)
    return {
        **report,
        "answer": _selected_answer(
            report["answer"],
            rows,
            placed,
            blocked_workers=blocked_count(report["workers"]),
        ),
        "rows": rows,
        "selection": {
            **_scope(pairing, placement, keys),
            "requested": requested,
            "eligible": len(by_bucket["eligible"]),
            "blocked": len(by_bucket["blocked"]),
            "stale": len(by_bucket["stale"]),
            "untested": len(by_bucket["untested"]),
            "lists": {bucket: _capped(by_bucket[bucket]) for bucket in BUCKETS},
            "held_by": held_by,
            # The area the dialog names: the first row holding a selected case, never a worker.
            "blocking_area": next(
                (
                    one["area"]
                    for one in held_by
                    if one["area"] != "workers" and one["count"] > 0
                ),
                None,
            ),
            "dropped": placement.dropped,
            "grading": {
                "gradable": requested - len(not_gradable),
                "not_gradable": not_gradable[:LIST_LIMIT],
                "truncated": len(not_gradable) > LIST_LIMIT,
            },
            "requirements_recorded": placement.recorded,
            "next": _unproved_next(pairing, bundle, placement),
            "running": requested - len(by_bucket["blocked"]),
            "held_back": {
                **_held_list(
                    held_back,
                    _case_names(placed),
                    _held_rows(placement),
                    _own_reasons(placement),
                ),
                "by": dict(held_counts),
            },
            "reasons": held_reasons,
        },
    }


def _worker_reasons(
    placed: list[Placed], workers: list[WorkerBoundaryView]
) -> dict[str, str]:
    """Why each `worker:<id>` holding a selected case back holds it: the worker's row reason for a bypass, else ALK's
    sealed cause for the first case it holds."""
    bypassed = {
        f"worker:{one}": reason for one, reason in bypassed_workers(workers).items()
    }
    reasons: dict[str, str] = {}
    for one in placed:
        if one.bucket != "blocked":
            continue
        sealed = _worker_keys(one.hold) if one.hold.held else []
        for key in one.by:
            if key in sealed and one.hold.reason:
                reasons.setdefault(key, one.hold.reason)
            elif key in bypassed:
                reasons.setdefault(key, bypassed[key])
    return reasons


def _worker_keys(hold: CaseHold) -> list[str]:
    """The `worker:<id>` keys a held case is held by: the workers ALK sealed as unheld for it; `worker_limits` with
    its sealed cause when it named none, or sealed before it named them."""
    return [f"worker:{one}" for one in hold.unheld or ()] or [WORKER_LIMITS_HELD]


def _holds(row: Any, one: Placed) -> bool:
    """Whether a row holds this case back: a blocked row a case it blocks, never one it only reads stale or untested
    for; any other row every case it holds."""
    if row["status"] == "blocked":
        return one.bucket == "blocked" and row["id"] in one.by
    return row["id"] in one.held_by


def _row_holds(row: Any, placed: list[Placed]) -> list[str]:
    """The selected cases a row holds back."""
    return [one.case.key for one in placed if _holds(row, one)]


def _held_by(
    rows: list[SelectionRowView],
    workers: list[WorkerBoundaryView],
    placed: list[Placed],
) -> list[HeldBy]:
    """Each blocked row, then each worker, that holds selected cases back, with how many."""
    by_worker = Counter(
        key
        for one in placed
        if one.bucket == "blocked"
        for key in one.by
        if key.startswith("worker:") or key == WORKER_LIMITS_HELD
    )
    reasons = _worker_reasons(placed, workers)
    names = {f"worker:{one['worker_id']}": one["name"] for one in workers}
    held: list[HeldBy] = [
        {
            "id": row["id"],
            "name": row["label"],
            "area": row["area"],
            "count": row["affected"]["count"],
            "reason": row["reason"],
            "text": row["reason"] or row["label"],
        }
        for row in rows
        if row["status"] == "blocked" and row["affected"]["count"] > 0
    ]
    held += [
        {
            "id": key,
            "name": names.get(key, key.removeprefix("worker:")),
            "area": "workers",
            "count": count,
            "reason": reasons.get(key),
            "text": f"{names.get(key, 'A worker')}'s limits are not held",
        }
        for key, count in sorted(by_worker.items())
    ]
    return held


def _selected_answer(
    found: AnswerView,
    rows: list[SelectionRowView],
    placed: list[Placed],
    *,
    blocked_workers: int,
) -> AnswerView:
    """The answer for the selected cases: a world tool the agent version lacks can only
    be missed by the cases that use it, so when no selected case does, it does not
    block the answer. Only when every selected case's needs were read: an unread need
    may be that very tool. A tool reaching production still blocks: the agent can call
    it in any case."""
    if not placed or any(not one.case.recorded or one.case.unknown for one in placed):
        return found
    unused = {
        row["id"]
        for row in rows
        if row["status"] == "blocked"
        and row["reason_code"] == "tool_not_in_agent"
        and row["targets"]
        and all(target["kind"] == "tool" for target in row["targets"])
        and row["affected"]["count"] == 0
    }
    if not unused or found["kind"] != "blocked" or found["reason_code"] is not None:
        return found
    return answer(
        [_supported(row) if row["id"] in unused else row for row in rows],
        validating=False,
        failed=False,
        named=False,
        blocked_workers=blocked_workers,
    )


class HeldBack(TypedDict):
    count: int
    keys: list[str]
    truncated: bool
    # Each listed case's current name, by its key; a case without one is shown by its key.
    names: dict[str, str]
    # Held-back cases per row that held them.
    by: dict[str, int]
    # The row each listed case is counted under in ``by``, by its key.
    by_key: dict[str, str]
    # Each listed case's own reason, by its key, where its lifecycle gives one: never another case's.
    reason_by_key: NotRequired[dict[str, str]]


class RunSelection(Scope):
    requested: int
    running: int
    held_back: HeldBack
    # Each holding row's reason, in plain words.
    reasons: dict[str, str]
    next: validation.UnprovedNext | None
    # Set when cases held only for validation run anyway, because validation cannot run here.
    validation: NotRequired[Literal["not_enforced"]]


@dataclass(frozen=True)
class RunPlan:
    """What a run of ``keys`` sends: every requested case but the Blocked ones."""

    view: RunSelection
    held: frozenset[str]
    # The report the decision was made from, and the evidence revision it read.
    report: ReadinessReport
    revision: str


def _held_list(
    held: list[str],
    names: Mapping[str, str],
    rows: Mapping[str, str] | None = None,
    own: Mapping[str, str] | None = None,
) -> HeldBack:
    """The held-back cases as the run dialog lists them, each with its current name, the row it is counted under and
    its own reason."""
    listed = _capped(held)
    shown: HeldBack = {
        "count": len(held),
        **listed,
        "names": {key: names[key] for key in listed["keys"] if names.get(key)},
        "by": {},
        "by_key": {
            key: (rows or {})[key] for key in listed["keys"] if key in (rows or {})
        },
    }
    if own_reasons := {
        key: (own or {})[key] for key in listed["keys"] if (own or {}).get(key)
    }:
        shown["reason_by_key"] = own_reasons
    return shown


def _own_reasons(placement: Any) -> dict[str, str]:
    """Each held-back case's own lifecycle reason, by its key."""
    return {
        one.case.key: one.lifecycle_reason
        for one in placement.placed
        if one.bucket == "blocked" and one.lifecycle_reason
    }


def _held_rows(placement: Any) -> dict[str, str]:
    """The row each held-back case counts under: its own lifecycle reason when it has one, else the first row holding
    it; a case certifying the pair dropped counts as dropped."""
    rows = {
        one.case.key: next((row for row in one.by if row in _CASE_ROWS), one.by[0])
        for one in placement.placed
        if one.bucket == "blocked" and one.by
    }
    for one in placement.dropped:
        if one["code"] == "scenario_dropped":
            rows[one["key"]] = DROPPED_ROW
    return rows


# The rows that hold a case for its own lifecycle, not for the pairing: a held case is counted under one of these.
_CASE_ROWS = (
    NOT_VALIDATED_ROW,
    ARCHIVED_ROW,
    NOT_ACTIVE_ROW,
    PROOF_FAILED_ROW,
    SEED_UNREAD_ROW,
    TOOL_MISSING_ROW,
)


def _held_back(
    placement: Any, rows: list[Any], workers: list[WorkerBoundaryView]
) -> tuple[list[str], Counter[str], dict[str, str]]:
    """The cases a run of the placed selection holds back, per holding row or worker, with each one's reason."""
    dropped = [
        one["key"] for one in placement.dropped if one["code"] == "scenario_dropped"
    ]
    placed = placement.placed
    held = [one.case.key for one in placed if one.bucket == "blocked"] + dropped
    # Each case counts once, under its own lifecycle reason when it has one, else the first row holding it, so the
    # counts add up to the cases held back.
    primary = _held_rows(placement)
    # A case held only until it is validated comes after one held for something to fix, so the list leads with those.
    held.sort(key=lambda key: primary.get(key) == NOT_VALIDATED_ROW)
    by = Counter(primary.values())
    reasons = {
        row["id"]: row["reason"] or row["label"] for row in rows if row["id"] in by
    }
    if NO_DOOR_ROW in by:
        reasons[NO_DOOR_ROW] = NO_DOOR_REASON
    # A worker is not a row: a case it holds back reads ALK's sealed cause, or its bypass.
    reasons.update(_worker_reasons(placed, workers))
    for row in _CASE_ROWS:
        texts = [
            one.lifecycle_reason
            for one in placed
            if primary.get(one.case.key) == row and one.lifecycle_reason
        ]
        if texts:
            reasons[row] = "; ".join(dict.fromkeys(texts))
    if dropped:
        by[DROPPED_ROW] = len(dropped)
        reasons[DROPPED_ROW] = DROPPED_REASON
    return held, by, reasons


def _unproved_next(
    pairing: Pairing, bundle: HarnessAssembledBundle | None, placement: _Placement
) -> validation.UnprovedNext | None:
    """What a run does when every selected case is held back only for not being validated on the pairing; the run
    route acts on this same value."""
    if (
        not placement.placed
        or placement.dropped
        or any(one.by != [NOT_VALIDATED_ROW] for one in placement.placed)
    ):
        return None
    return validation.unproved_next(pairing.environment, pairing.agent_version, bundle)


def select_for_run(
    pairing: Pairing, *, keys: list[str], trials: int, lifecycle_gate: bool = True
) -> RunPlan:
    """The requested cases a run holds back, placed as ``selection_report`` places
    them: a case in the blocked bucket is held back and named, never sent.

    Raises:
        HostedHarnessError: as ``selection_report``; 503 ``readiness_unavailable``
            (retryable) when the pair's certification cannot be read. A selected case
            certifying the pair dropped is held back and named, never sent.
    """
    bundle = pair_bundle(pairing)
    revision = evidence_revision(pairing, bundle)
    evidence = read_evidence(pairing, bundle)
    if evidence.unreadable:
        raise HostedHarnessError(
            "readiness_unavailable",
            "Couldn't read whether these versions are ready, so no run was created."
            " Try again in a moment.",
            status_code=503,
            retryable=True,
        )
    report = readiness_report(pairing, bundle=bundle, evidence=evidence)
    placement = _place_all(
        pairing,
        report["rows"],
        keys=keys,
        trials=trials,
        bundle=bundle,
        evidence=evidence,
        workers=report["workers"],
        lifecycle_gate=lifecycle_gate,
    )
    held, by, reasons = _held_back(placement, report["rows"], report["workers"])
    return RunPlan(
        view={
            **_scope(pairing, placement, keys),
            "requested": len(keys),
            "running": len(keys) - len(held),
            "held_back": {
                **_held_list(
                    held,
                    _case_names(placement.placed),
                    _held_rows(placement),
                    _own_reasons(placement),
                ),
                "by": dict(by),
            },
            "reasons": reasons,
            "next": _unproved_next(pairing, bundle, placement),
        },
        held=frozenset(held),
        report=report,
        revision=revision,
    )


def case_page(
    pairing: Pairing,
    *,
    keys: list[str] | None,
    trials: int,
    bucket: Bucket | None,
    row: str | None,
    cursor: int | None,
) -> CasePage:
    """One page of the selected cases in a bucket or held by a row, by number.

    ``cursor`` is the number the previous page ended on.

    Raises:
        HostedHarnessError: as ``selection_report``.
    """
    bundle = pair_bundle(pairing)
    evidence = read_evidence(pairing, bundle)
    report = readiness_report(pairing, bundle=bundle, evidence=evidence)
    placed = _place_all(
        pairing,
        report["rows"],
        keys=keys,
        trials=trials,
        bundle=bundle,
        evidence=evidence,
        workers=report["workers"],
    ).placed
    # A row's cases are the ones its count names (`_holds`); a key that is no report row matches as before.
    shown = next((one for one in report["rows"] if one["id"] == row), None)
    matching = [
        one
        for one in placed
        if (bucket is None or one.bucket == bucket)
        and (
            row is None
            or (_holds(shown, one) if shown is not None else row in one.held_by)
        )
        and (cursor is None or one.case.number > cursor)
    ]
    page = matching[:PAGE_SIZE]
    return {
        "items": [
            {
                "key": one.case.key,
                "number": one.case.number,
                "name": one.case.name,
                "persona": one.case.persona,
                "by": one.by,
            }
            for one in page
        ],
        "next": str(page[-1].case.number) if len(matching) > PAGE_SIZE else None,
    }


def _scope(pairing: Pairing, placement: _Placement, keys: list[str] | None) -> Scope:
    """The suite's size, the cases not selected and the selected cases changed since
    this agent and environment version last ran."""
    wanted = set(keys) if keys is not None else None
    excluded = [
        key for key in placement.suite if wanted is not None and key not in wanted
    ]
    selected = [key for key in placement.suite if wanted is None or key in wanted]
    since, changed = changed_since_last_run(pairing, selected)
    return {
        "suite_total": len(placement.suite),
        "excluded": {
            "count": len(excluded),
            **_capped(excluded),
            "reason": "not_selected",
        },
        "changed": {"count": len(changed), **_capped(changed), "since": since},
    }


def changed_since_last_run(
    pairing: Pairing, keys: list[str]
) -> tuple[ChangedSince | None, list[str]]:
    """The newest run of the pairing's agent and environment version, and which of
    ``keys`` it ran with other contents, or did not have: a case whose stored contents
    differ by digest from that run's suite version. No run: none changed."""
    last = (
        HostedHarnessJob.no_workspace_objects.filter(
            ~validation_jobs(),
            environment_id=pairing.environment.id,
            agent_version_id=getattr(pairing.agent_version, "id", None),
            environment_version_id=pairing.environment_version.id,
            test_execution_id__isnull=False,
            suite_version__isnull=False,
        )
        .select_related("suite_version")
        .order_by("-created_at")
        .first()
    )
    if last is None:
        return None, []
    since: ChangedSince = {
        "run_id": str(last.id),
        "suite_version": cast(
            "VersionRef | None", version_refs(None, None, last.suite_version)["suite"]
        ),
    }
    before = _digests(last.suite_version_id, keys)
    now = _digests(pairing.suite_version.id, keys)
    return since, [key for key in keys if before.get(key) != now.get(key)]


def _supported(row: SelectionRowView) -> SelectionRowView:
    supported: SelectionRowView = {**row, "status": "supported"}
    return supported


def _digests(suite_version_id: UUID | None, keys: list[str]) -> dict[str, str]:
    return {
        key: canonical_digest(content)
        for key, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_version_id, scenario_key__in=keys
        ).values_list("scenario_key", "content")
    }


def _case_names(placed: Iterable[Any]) -> dict[str, str]:
    return {one.case.key: one.case.name for one in placed if one.case.name}


def _capped(keys: list[str]) -> CaseList:
    return {"keys": keys[:LIST_LIMIT], "truncated": len(keys) > LIST_LIMIT}


def _place_all(
    pairing: Pairing,
    rows: list[RowView],
    *,
    keys: list[str] | None,
    trials: int,
    bundle: HarnessAssembledBundle | None,
    evidence: Evidence,
    workers: list[WorkerBoundaryView],
    lifecycle_gate: bool = True,
) -> _Placement:
    cases = case_needs(
        environment_version=pairing.environment_version,
        suite_version=pairing.suite_version,
    )
    selected = _selected(cases, keys)
    dropped_tokens = _dropped_tokens(bundle)
    kept: list[CaseNeeds] = []
    dropped: list[DroppedCase] = []
    for one in selected:
        if scenario_token(one.key) in dropped_tokens:
            dropped.append(
                {"key": one.key, "code": "scenario_dropped", "reason": DROPPED_REASON}
            )
        elif one.ungradable:
            # The run route refuses it: it could not score it.
            dropped.append(
                {
                    "key": one.key,
                    "code": "scenario_ungradable",
                    "reason": UNGRADABLE_REASON,
                }
            )
        else:
            kept.append(one)
    # A fix's own check run measures the cases it wrote for the fix: it is gated by readiness, not by lifecycle.
    lifecycle = (
        scenario_lifecycle(
            pairing.environment,
            suite_version=pairing.suite_version,
            environment_version=pairing.environment_version,
            keys=[one.key for one in kept],
            agent_version_id=getattr(pairing.agent_version, "id", None),
        )
        if lifecycle_gate
        else {}
    )
    # A run of a pairing being validated, or a validation failed, is refused, whatever
    # its rows say.
    pair_validating = (
        bundle is not None and validation.live_validation(bundle.id) is not None
    )
    pair_failed = validation.failed_check(bundle)
    # A certificate made before the check recorded it, or one that did not run it, reports nothing.
    judge_failed = evidence.checks.get("judge_reachable") == "failed"
    by_id = {row["id"]: row for row in rows}
    voice = _voice(pairing)
    reach = _door_reach(pairing)
    many = len(kept) > 1 or trials > 1
    holds = case_holds(pairing, evidence)
    bypassed = frozenset(bypassed_workers(workers))
    placed = [
        _place(
            case,
            by_id,
            view=lifecycle.get(case.key),
            voice=voice,
            many=many,
            pair_validating=pair_validating,
            pair_failed=pair_failed,
            no_door=_needs_a_missing_door(case, reach),
            judge_failed=judge_failed,
            hold=case_hold(holds, case.key),
            bypassed=bypassed,
        )
        for case in kept
    ]
    return _Placement(
        placed=placed,
        dropped=dropped,
        recorded=all(one.recorded for one in kept),
        suite=[one.key for one in cases],
    )


def _selected(cases: list[CaseNeeds], keys: list[str] | None) -> list[CaseNeeds]:
    """The requested cases in number order, refused as the run route refuses them."""
    if keys is None:
        return cases
    if not keys:
        raise HostedHarnessError(
            "scenario_selection_empty", "select at least one scenario", status_code=400
        )
    if len(keys) != len(set(keys)):
        raise HostedHarnessError(
            "scenario_selection_duplicate",
            "scenario selection must not contain duplicates",
            status_code=400,
        )
    wanted = set(keys)
    known = {one.key for one in cases}
    missing = [key for key in keys if key not in known]
    if missing:
        raise HostedHarnessError(
            "scenario_selection_unknown",
            ", ".join(missing)
            + (" is" if len(missing) == 1 else " are")
            + " not in this scenario version",
            status_code=400,
        )
    return [one for one in cases if one.key in wanted]


def _dropped_tokens(bundle: HarnessAssembledBundle | None) -> frozenset[str]:
    return frozenset(
        scenario_token(key)
        for warning in (bundle.warnings if bundle is not None else None) or []
        if isinstance(warning, Mapping) and warning.get("code") == "scenario_dropped"
        for key in warning.get("keys") or []
    )


def _door_reach(pairing: Pairing) -> dict[str, bool]:
    """Whether the world's door stands in front of each contract tool, by tool name and by the world action a fault
    names: a tools-service tool is behind the tool proxy, a Python agent's module-level callable or method behind
    the door in its own process; anything else runs where no door stands. Empty when the contract names no
    entrypoints (nothing to tell, nothing held back). A tool the build found out of the door's reach says so
    (`door_unreached`). The same rule as the actors' `harness_actors.door_reach`."""
    contract = (pairing.environment_version.documents or {}).get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    raw_runtime = contract.get("runtime")
    runtime: Mapping = raw_runtime if isinstance(raw_runtime, Mapping) else {}
    python = str(runtime.get("language") or "python").strip().lower() in (
        "",
        "python",
        "py",
    )
    reach: dict[str, bool] = {}
    for entry in contract.get("tool_entrypoints") or []:
        if not isinstance(entry, Mapping) or not entry.get("tool"):
            continue
        mode = str(entry.get("mode") or "")
        route = str(entry.get("endpoint") or "").strip("/")
        reachable = bool(
            (mode == "service" and route)
            or (
                python
                and mode in ("import", "construct")
                and entry.get("module")
                and entry.get("callable")
                and not entry.get("door_unreached")
            )
        )
        reach[str(entry["tool"])] = reachable
        if route:
            reach[route.replace("/", ".")] = reachable
    return reach


def _needs_a_missing_door(case: CaseNeeds, reach: Mapping[str, bool]) -> bool:
    """Whether the case needs the world's door where it does not stand: a fault on a tool the door cannot hold, or an
    acting actor on an agent none of whose tools the door can reach."""
    if not reach or not case.door:
        return False
    if ACTING in case.door and not any(reach.values()):
        return True
    return any(reach.get(action) is False for action in case.door if action != ACTING)


def _voice(pairing: Pairing) -> bool:
    contract = (pairing.environment_version.documents or {}).get("contract")
    modality = contract.get("modality") if isinstance(contract, Mapping) else None
    return str(modality or "").lower() == "voice"


def _status_for(row: RowView, case: CaseNeeds) -> Status:
    """The row's status for this case: a tool row failing only on tools the case
    does not need is Supported for it, unless its evidence is Stale or waits on its
    gate. An agent tool the world lacks may be called in any case."""
    if (
        row["id"] in TOOL_ROWS
        and row["targets"]
        and not any(
            target["kind"] == "agent_tool" or target["name"] in case.tools
            for target in row["targets"]
        )
    ):
        if (
            row["stale_because"] in ("built_with_unknown", "saved_without_check")
            or row["blocked_by"]
        ):
            return "untested"
        return "stale" if row["stale_because"] else "supported"
    return row["status"]


def _place(
    case: CaseNeeds,
    rows: Mapping[str, RowView],
    *,
    view: Any,
    voice: bool,
    many: bool,
    pair_validating: bool,
    pair_failed: bool,
    no_door: bool = False,
    judge_failed: bool = False,
    hold: CaseHold = NO_HOLD,
    bypassed: frozenset[str] = frozenset(),
) -> Placed:
    needed = set(case.rows)
    if voice:
        # A voice case reaches the agent by audio, not by a test chat message.
        needed.discard("message")
        needed.add("audio")
    # Every case needs a row naming an agent tool the world lacks.
    needed.update(
        one
        for one, row in rows.items()
        if any(target["kind"] == "agent_tool" for target in row["targets"])
    )
    reset = rows.get("reset")
    if many and reset is not None and reset["finding"] != "not_applicable":
        # In a seeded world each case starts from what an earlier one left behind.
        needed.add("reset")
    statuses = {
        one: _status_for(rows[one], case)
        for one in _MEASURED
        if one in needed and one in rows
    }
    held = frozenset(one for one, status in statuses.items() if status in _HOLDING)
    # Unavailable means this connection kind does not have it: the case still runs.
    blocked = [one for one, status in statuses.items() if status == "blocked"]
    if pair_failed:
        blocked.append("pair_failed")
    # A kept-as-written case is runnable: it is placed by its other rows and carries its warning.
    lifecycle_reason = None
    if view is not None and view["state"] == "archived":
        blocked.append(ARCHIVED_ROW)
        lifecycle_reason = view["reason_text"]
    elif view is not None and view["state"] == "validated":
        blocked.append(NOT_ACTIVE_ROW)
        lifecycle_reason = reason_text("not_active")
    elif not runnable(view) and view["reason"] in UNVALIDATED_REASONS:
        blocked.append(NOT_VALIDATED_ROW)
        lifecycle_reason = view["reason_text"]
    elif not runnable(view) and view["reason"] in UNREAD_REASONS:
        blocked.append(SEED_UNREAD_ROW)
        lifecycle_reason = view["reason_text"]
    elif not runnable(view) and view["reason"] == TOOL_MISSING_ROW:
        blocked.append(TOOL_MISSING_ROW)
        lifecycle_reason = view["reason_text"]
    elif not runnable(view):
        blocked.append(PROOF_FAILED_ROW)
        lifecycle_reason = view["reason_text"]
    if no_door:
        blocked.append(NO_DOOR_ROW)
    if hold.held:
        # ALK's run would hold it as not graded: the worker limits it relies on are not held.
        blocked.extend(_worker_keys(hold))
    elif hold.relies:
        # It relies on the limits of a worker a recent run saw bypass its channel.
        blocked.extend(f"worker:{one}" for one in hold.workers if one in bypassed)
    stale = [one for one, status in statuses.items() if status == "stale"]
    untested = [one for one, status in statuses.items() if status == "untested"]
    if case.unknown:
        untested.append("unknown_need")
    if hold.held is None:
        untested.append(WORKER_LIMITS_UNKNOWN)
    order: tuple[tuple[Bucket, list[str]], ...] = (
        (("untested", ["pair_validating"]),)
        if pair_validating
        else (("blocked", blocked), ("stale", stale), ("untested", untested))
    )
    bucket, by = next(
        ((name, reasons) for name, reasons in order if reasons), ("eligible", [])
    )
    return Placed(
        case=case,
        bucket=bucket,
        by=by,
        held_by=held,
        needed=frozenset(needed),
        gradable=_grading(case, statuses, judge_failed=judge_failed),
        hold=hold,
        lifecycle_reason=lifecycle_reason,
    )


def _grading(
    case: CaseNeeds, statuses: Mapping[str, Status], *, judge_failed: bool = False
) -> NotGradable | None:
    """Why the case cannot be graded: a check that cannot be read, a judged sub-goal
    the judge could not be reached for in the check, or a needed evidence row neither
    Supported nor Partial."""
    if case.grading_gap is not None:
        return {"key": case.key, "reason": case.grading_gap}
    if case.judged and judge_failed:
        return {"key": case.key, "reason": "judge_unavailable"}
    if any(
        status not in GATE_OPEN
        for one, status in statuses.items()
        if one in _EVIDENCE_ROWS
    ):
        return {"key": case.key, "reason": "evidence_not_supported"}
    return None
