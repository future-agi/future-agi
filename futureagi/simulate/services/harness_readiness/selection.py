"""Which selected cases a pairing's readiness holds back, and why.

Each case lands in exactly one bucket, first match: blocked, stale, untested, then
eligible. A row holds a case only when the case needs it; a tool row whose targets
name none of the case's tools reads Supported for it, and an unmeasured row holds no
case. While the pairing is being checked, every case reads untested: the run gate
refuses it and the check has not answered yet.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypedDict

from simulate.models import HarnessAssembledBundle, HarnessSuiteValidation
from simulate.services.harness_readiness.catalogue import (
    CATALOGUE,
    GATE_OPEN,
    AnswerView,
    AreaView,
    Pairing,
    PairingView,
    RowView,
    Status,
)
from simulate.services.harness_readiness.evidence import certified, pair_bundle
from simulate.services.harness_readiness.report import readiness_report
from simulate.services.harness_scenario_needs import TOOL_ROWS, CaseNeeds, case_needs
from simulate.services.harness_versioning import validation
from simulate.services.hosted_harness import HostedHarnessError

Bucket = Literal["eligible", "blocked", "stale", "untested"]
BUCKETS: tuple[Bucket, ...] = ("eligible", "blocked", "stale", "untested")
LIST_LIMIT = 200
PAGE_SIZE = 50
DROPPED_REASON = "dropped when the pair was certified"
UNGRADABLE_REASON = "grades no sub-goal of this environment version"
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
    reason: Literal["check_unreadable", "no_checks", "evidence_not_supported"]


class Grading(TypedDict):
    gradable: int
    not_gradable: list[NotGradable]
    truncated: bool


class SelectionView(TypedDict):
    requested: int
    eligible: int
    blocked: int
    stale: int
    untested: int
    lists: dict[Bucket, CaseList]
    dropped: list[DroppedCase]
    grading: Grading
    requirements_recorded: bool


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
    counts: dict[Status, int]
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
    ``pair_validating`` / ``pair_failed`` / ``not_proved_here`` / ``unknown_need``),
    and the rows holding it."""

    case: CaseNeeds
    bucket: Bucket
    by: list[str]
    held_by: frozenset[str]
    needed: frozenset[str]
    gradable: NotGradable | None


@dataclass(frozen=True)
class _Placement:
    placed: list[Placed]
    dropped: list[DroppedCase]
    recorded: bool


def selection_report(
    pairing: Pairing, *, keys: list[str] | None, trials: int
) -> SelectionReport:
    """The pairing's readiness with the selected cases bucketed; ``keys`` ``None``
    selects the whole suite. No writes.

    Raises:
        HostedHarnessError: 400 ``scenario_selection_empty``,
            ``scenario_selection_duplicate`` or ``scenario_selection_unknown``.
    """
    report = readiness_report(pairing)
    placement = _place_all(pairing, report["rows"], keys=keys, trials=trials)
    placed = placement.placed
    requested = len(placed)
    by_bucket: dict[Bucket, list[str]] = {bucket: [] for bucket in BUCKETS}
    for one in placed:
        by_bucket[one.bucket].append(one.case.key)
    not_gradable = [one.gradable for one in placed if one.gradable is not None]
    rows: list[SelectionRowView] = []
    for row in report["rows"]:
        held = [one.case.key for one in placed if row["id"] in one.held_by]
        rows.append(
            {
                **row,
                "needed_by": sum(1 for one in placed if row["id"] in one.needed),
                "affected": {"count": len(held), "of": requested, **_capped(held)},
            }
        )
    return {
        **report,
        "rows": rows,
        "selection": {
            "requested": requested,
            **{bucket: len(by_bucket[bucket]) for bucket in BUCKETS},
            "lists": {bucket: _capped(by_bucket[bucket]) for bucket in BUCKETS},
            "dropped": placement.dropped,
            "grading": {
                "gradable": requested - len(not_gradable),
                "not_gradable": not_gradable[:LIST_LIMIT],
                "truncated": len(not_gradable) > LIST_LIMIT,
            },
            "requirements_recorded": placement.recorded,
        },
    }


class HeldBack(TypedDict):
    count: int
    keys: list[str]
    truncated: bool
    # Held-back cases per row that held them.
    by: dict[str, int]


class RunSelection(TypedDict):
    requested: int
    running: int
    held_back: HeldBack
    # Each holding row's reason, in plain words.
    reasons: dict[str, str]


@dataclass(frozen=True)
class RunPlan:
    """What a run of ``keys`` sends: every requested case but the Blocked ones."""

    view: RunSelection
    held: frozenset[str]


def select_for_run(pairing: Pairing, *, keys: list[str], trials: int) -> RunPlan:
    """The requested cases a run holds back, placed as ``selection_report`` places
    them: a case in the blocked bucket is held back and named, never sent.

    Raises:
        HostedHarnessError: as ``selection_report``.
    """
    report = readiness_report(pairing)
    placed = _place_all(pairing, report["rows"], keys=keys, trials=trials).placed
    held = [one.case.key for one in placed if one.bucket == "blocked"]
    by = Counter(row for one in placed if one.bucket == "blocked" for row in one.by)
    reasons = {
        row["id"]: row["reason"] or row["label"]
        for row in report["rows"]
        if row["id"] in by
    }
    return RunPlan(
        view={
            "requested": len(keys),
            "running": len(keys) - len(held),
            "held_back": {"count": len(held), **_capped(held), "by": dict(by)},
            "reasons": reasons,
        },
        held=frozenset(held),
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
    report = readiness_report(pairing)
    placed = _place_all(pairing, report["rows"], keys=keys, trials=trials).placed
    matching = [
        one
        for one in placed
        if (bucket is None or one.bucket == bucket)
        and (row is None or row in one.held_by)
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


def _capped(keys: list[str]) -> CaseList:
    return {"keys": keys[:LIST_LIMIT], "truncated": len(keys) > LIST_LIMIT}


def _place_all(
    pairing: Pairing, rows: list[RowView], *, keys: list[str] | None, trials: int
) -> _Placement:
    from simulate.services.hosted_harness_gateway import _scenario_token

    cases = case_needs(
        environment_version=pairing.environment_version,
        suite_version=pairing.suite_version,
    )
    selected = _selected(cases, keys)
    bundle = pair_bundle(pairing)
    dropped_tokens = _dropped_tokens(bundle)
    kept: list[CaseNeeds] = []
    dropped: list[DroppedCase] = []
    for one in selected:
        if _scenario_token(one.key) in dropped_tokens:
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
    proved = _proved_tokens(pairing)
    ready = certified(bundle)
    # A run of a pairing being validated, or a validation failed, is refused, whatever
    # its rows say.
    pair_validating = (
        bundle is not None and validation.live_validation(bundle.id) is not None
    )
    pair_failed = validation.failed_check(bundle)
    by_id = {row["id"]: row for row in rows}
    voice = _voice(pairing)
    many = len(kept) > 1 or trials > 1
    placed = [
        _place(
            case,
            by_id,
            proved=ready or _scenario_token(case.key) in proved,
            voice=voice,
            many=many,
            pair_validating=pair_validating,
            pair_failed=pair_failed,
        )
        for case in kept
    ]
    return _Placement(
        placed=placed,
        dropped=dropped,
        recorded=all(one.recorded for one in kept),
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
    from simulate.services.hosted_harness_gateway import _scenario_token

    return frozenset(
        _scenario_token(key)
        for warning in (bundle.warnings if bundle is not None else None) or []
        if isinstance(warning, Mapping) and warning.get("code") == "scenario_dropped"
        for key in warning.get("keys") or []
    )


def _proved_tokens(pairing: Pairing) -> frozenset[str]:
    """Scenarios with a holding proof for this suite and environment version."""
    from simulate.services.hosted_harness_gateway import _scenario_token

    proofs = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=pairing.suite_version,
            environment_version=pairing.environment_version,
        )
        .values_list("proofs", flat=True)
        .first()
    )
    return frozenset(
        _scenario_token(key)
        for key, proof in (proofs or {}).items()
        if isinstance(proof, Mapping) and proof.get("holds")
    )


def _voice(pairing: Pairing) -> bool:
    contract = (pairing.environment_version.documents or {}).get("contract")
    modality = contract.get("modality") if isinstance(contract, Mapping) else None
    return str(modality or "").lower() == "voice"


def _status_for(row: RowView, case: CaseNeeds) -> Status:
    """The row's status for this case: a tool row failing only on tools the case
    does not need is Supported for it. An agent tool the world lacks may be called
    in any case."""
    if (
        row["id"] in TOOL_ROWS
        and row["targets"]
        and not any(
            target["kind"] == "agent_tool" or target["name"] in case.tools
            for target in row["targets"]
        )
    ):
        return "supported"
    return row["status"]


def _place(
    case: CaseNeeds,
    rows: Mapping[str, RowView],
    *,
    proved: bool,
    voice: bool,
    many: bool,
    pair_validating: bool,
    pair_failed: bool,
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
    blocked = [
        one for one, status in statuses.items() if status in ("blocked", "unavailable")
    ]
    if pair_failed:
        blocked.append("pair_failed")
    stale = [one for one, status in statuses.items() if status == "stale"]
    if not proved:
        stale.append("not_proved_here")
    untested = [one for one, status in statuses.items() if status == "untested"]
    if case.unknown:
        untested.append("unknown_need")
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
        gradable=_grading(case, statuses),
    )


def _grading(case: CaseNeeds, statuses: Mapping[str, Status]) -> NotGradable | None:
    """Why the case cannot be graded: a check that cannot be read, or a needed
    evidence row neither Supported nor Partial."""
    if case.grading_gap is not None:
        return {"key": case.key, "reason": case.grading_gap}
    if any(
        status not in GATE_OPEN
        for one, status in statuses.items()
        if one in _EVIDENCE_ROWS
    ):
        return {"key": case.key, "reason": "evidence_not_supported"}
    return None
