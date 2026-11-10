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
from datetime import datetime, timezone
from typing import Literal, TypedDict

from simulate.models import (
    HarnessAssembledBundle,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HostedHarnessJob,
)
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
)
from simulate.services.harness_readiness.evidence import certified, pair_bundle
from simulate.services.harness_readiness.report import readiness_report
from simulate.services.harness_readiness.rules import answer
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


class SelectionView(Scope):
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
        "answer": _selected_answer(report["answer"], rows, placed),
        "rows": rows,
        "selection": {
            **_scope(pairing, placement, keys),
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


ProofState = Literal["proved", "failed", "not_proved"]


class LastProved(TypedDict):
    environment_version: int | None
    suite_version: int | None


class ProofView(TypedDict):
    state: ProofState
    last_proved: LastProved | None


class ScenarioReadiness(TypedDict):
    bucket: Bucket | None
    by: list[str]
    # Each readiness row in ``by`` and its reason code, e.g. ``environment_changed``.
    reasons: dict[str, str | None]
    dropped: str | None
    proof: ProofView


def scenario_readiness(pairing: Pairing) -> dict[str, ScenarioReadiness]:
    """Each scenario of the pairing's suite version, by key: its bucket and what put it
    there, and where its proof stands. A dropped scenario has no bucket. One readiness
    read for the whole suite. No writes."""
    report = readiness_report(pairing)
    placement = _place_all(pairing, report["rows"], keys=None, trials=1)
    proof = _proof_views(pairing, certified(pair_bundle(pairing)))
    rows = {row["id"]: row for row in report["rows"]}
    found: dict[str, ScenarioReadiness] = {
        one.case.key: {
            "bucket": one.bucket,
            "by": list(one.by),
            "reasons": {
                name: rows[name]["reason_code"] for name in one.by if name in rows
            },
            "dropped": None,
            "proof": proof(one.case.key),
        }
        for one in placement.placed
    }
    for one in placement.dropped:
        found[one["key"]] = {
            "bucket": None,
            "by": [],
            "reasons": {},
            "dropped": one["code"],
            "proof": proof(one["key"]),
        }
    return found


def _proof_views(pairing: Pairing, ready: bool):
    """Where each scenario's proof stands for this suite and environment version:
    proved (or the pairing is certified), failed, or not proved; and, when not proved
    here, the latest versions it was last proved on, by a proof or a certified
    pairing. Three queries."""
    from simulate.services.hosted_harness_gateway import _scenario_token

    here: Mapping[str, object] = {}
    earlier: list[tuple[object, dict[str, object], LastProved]] = []
    for row in (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version__job_id=pairing.environment.id
        )
        .select_related("suite_version", "environment_version")
        .order_by("-proved_at")
    ):
        proofs = {
            _scenario_token(key): value
            for key, value in (row.proofs or {}).items()
            if isinstance(value, Mapping)
        }
        if (
            row.suite_version_id == pairing.suite_version.id
            and row.environment_version_id == pairing.environment_version.id
        ):
            here = proofs
        else:
            earlier.append(
                (
                    row.proved_at,
                    proofs,
                    {
                        "environment_version": row.environment_version.version_number,
                        "suite_version": row.suite_version.version_number,
                    },
                )
            )
    # A certified pairing proved every scenario of its suite version, on its
    # environment version, whichever agent version it was certified with.
    bundles = []
    for bundle in (
        HarnessAssembledBundle._base_manager.filter(
            job_id=pairing.environment.id, state=HarnessAssembledBundle.State.READY
        )
        .exclude(object_key="")
        .exclude(object_key__isnull=True)
        .select_related("environment_version", "suite_version")
    ):
        if (bundle.environment_version_id, bundle.suite_version_id) == (
            pairing.environment_version.id,
            pairing.suite_version.id,
        ):
            ready = True
        else:
            bundles.append(bundle)
    members: dict[object, list[str]] = {}
    for suite_id, key in HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id__in={bundle.suite_version_id for bundle in bundles}
    ).values_list("suite_version_id", "scenario_key"):
        members.setdefault(suite_id, []).append(key)
    for bundle in bundles:
        earlier.append(
            (
                bundle.updated_at,
                {
                    _scenario_token(key): {"holds": True}
                    for key in members.get(bundle.suite_version_id, [])
                },
                {
                    "environment_version": bundle.environment_version.version_number,
                    "suite_version": bundle.suite_version.version_number,
                },
            )
        )
    earlier.sort(
        key=lambda one: one[0] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    def view(key: str) -> ProofView:
        token = _scenario_token(key)
        mine = here.get(token)
        if ready or (isinstance(mine, Mapping) and mine.get("holds") is True):
            return {"state": "proved", "last_proved": None}
        last = next(
            (
                versions
                for _, proofs, versions in earlier
                if isinstance(proofs.get(token), Mapping)
                and proofs[token].get("holds") is True
            ),
            None,
        )
        state: ProofState = "failed" if isinstance(mine, Mapping) else "not_proved"
        return {"state": state, "last_proved": last}

    return view


def _selected_answer(
    found: AnswerView, rows: list[SelectionRowView], placed: list[Placed]
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
        [{**row, "status": "supported"} if row["id"] in unused else row for row in rows],
        validating=False,
        failed=False,
        named=False,
    )


class HeldBack(TypedDict):
    count: int
    keys: list[str]
    truncated: bool
    # Held-back cases per row that held them.
    by: dict[str, int]


class RunSelection(Scope):
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
    placement = _place_all(pairing, report["rows"], keys=keys, trials=trials)
    placed = placement.placed
    held = [one.case.key for one in placed if one.bucket == "blocked"]
    by = Counter(row for one in placed if one.bucket == "blocked" for row in one.by)
    reasons = {
        row["id"]: row["reason"] or row["label"]
        for row in report["rows"]
        if row["id"] in by
    }
    return RunPlan(
        view={
            **_scope(pairing, placement, keys),
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
    from simulate.services.harness_versioning.read import version_refs

    last = (
        HostedHarnessJob.no_workspace_objects.filter(
            environment_id=pairing.environment.id,
            agent_version_id=getattr(pairing.agent_version, "id", None),
            environment_version_id=pairing.environment_version.id,
            test_execution_id__isnull=False,
            suite_version__isnull=False,
            # A validation job never ran the agent's cases; its key is set only on them.
            payload__metadata__certify_only__isnull=True,
        )
        .select_related("suite_version")
        .order_by("-created_at")
        .first()
    )
    if last is None:
        return None, []
    since: ChangedSince = {
        "run_id": str(last.id),
        "suite_version": version_refs(None, None, last.suite_version)["suite"],
    }
    before = _digests(last.suite_version_id, keys)
    now = _digests(pairing.suite_version.id, keys)
    return since, [key for key in keys if before.get(key) != now.get(key)]


def _digests(suite_version_id: object, keys: list[str]) -> dict[str, str]:
    return {
        key: canonical_digest(content)
        for key, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_version_id, scenario_key__in=keys
        ).values_list("scenario_key", "content")
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
    # Unavailable means this connection kind does not have it: the case still runs.
    blocked = [one for one, status in statuses.items() if status == "blocked"]
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
