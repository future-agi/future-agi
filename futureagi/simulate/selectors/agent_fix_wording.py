"""The words an epoch's result is told in. The epoch page's read and the pull request's
body both take them from here, so the two never say different things: plain sentences,
which the pull request only escapes as Markdown."""

from functools import cached_property
from math import floor
from typing import Any

from pydantic import BaseModel, Field

from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import EvalGrading, EvalReview, VerdictTier
from simulate.selectors.agent_fix import inventory
from simulate.services.agent_fix_engine import ChangedFile
from simulate.services.harness_environment import environment_sub_goals

# The files a change line names; the rest are counted.
_FILES_NAMED = 3

# Said where the user ships, whether that opens a pull request or makes a default.
EVALS_INCOMPLETE = (
    "Platform evals did not finish on this candidate, so their results are missing."
    " Review it closely."
)


class CandidateWording(BaseModel):
    """What the epoch page and the pull request say about a candidate."""

    # Its pass rate and whether a must-pass scenario broke; blank before it has a rate.
    headline: str = ""
    # The must-pass check's result; blank before the check ran.
    must_pass: str = ""
    # The must-pass scenarios it broke, each counted once.
    must_pass_broken: int = 0
    # The files its patch changes; None when they were not counted.
    changes: str | None = None
    # What a reviewer must know before trusting it.
    caveats: list[str] = Field(default_factory=list)


def percent(rate: float) -> str:
    # Half up, as the fix page rounds: 62.5% reads 63% in both places.
    return f"{floor(rate * 100 + 0.5)}%"


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def stored_files(candidate: AgentFixCandidate) -> list[ChangedFile] | None:
    """The files of the patch that ships, as counted when the candidate was stored; None
    when they were not. Reading them never reads the patch."""
    if candidate.changed_files is None:
        return None
    return [
        ChangedFile(str(path), int(added), int(removed))
        for path, added, removed in candidate.changed_files
    ]


def changes_line(files: list[ChangedFile]) -> str:
    """The whole change in one sentence: the files it changes and its lines."""
    if not files:
        return "Changes no files."
    names = [one.path for one in files[:_FILES_NAMED]]
    more = len(files) - len(names)
    if more:
        names.append(plural(more, "more file", "more files"))
    listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    added = sum(one.added for one in files)
    removed = sum(one.removed for one in files)
    return (
        f"Changes {listed}: {plural(added, 'line', 'lines')} added, {removed} removed."
    )


def _scenarios(checks: list[str] | None) -> int:
    """How many scenarios these checks are on: a check is sent as "scenario key: check",
    and one scenario may have several."""
    return len({str(check).partition(": ")[0] for check in checks or []})


def _noise(verdict: dict) -> str:
    """The must-pass scenarios that passed less often within normal noise."""
    moved = _scenarios(verdict.get("must_pass_within_noise"))
    return f" ({moved} moved within normal noise)" if moved else ""


def _must_pass_status(verdict: dict, tiers: list[VerdictTier]) -> str:
    """The must-pass check's status; a verdict from before the checks were recorded
    passed it when it broke nothing."""
    tier = next((one for one in tiers if one.name == "must-pass"), None)
    if tier is not None:
        return tier.status
    if not verdict or tiers:
        return "pending"
    return "failed" if verdict.get("must_pass_regressions") else "passed"


def _broke(verdict: dict) -> str:
    broke = _scenarios(verdict.get("must_pass_regressions"))
    if not broke:
        return "A must-pass scenario broke."
    return f"{plural(broke, 'must-pass scenario', 'must-pass scenarios')} broke."


def must_pass_line(verdict: dict, plan: dict, tiers: list[VerdictTier]) -> str:
    """The must-pass check's result; blank when it did not run or could not tell. A
    scenario that dropped within normal noise is not counted as broken, but it is said."""
    status = _must_pass_status(verdict, tiers)
    if status == "failed":
        return _broke(verdict)
    if status != "passed":
        return ""
    total, protected = plan.get("must_pass"), plan.get("protected")
    if not total:
        return f"No must-pass scenario broke{_noise(verdict)}."
    kept = f", including the {protected} you protected" if protected else ""
    return f"None of the {total} must-pass scenarios broke{kept}{_noise(verdict)}."


def headline(verdict: dict, tiers: list[VerdictTier]) -> str:
    """The result in one line: the held-back pass rate, or the target's when none was
    held back, then whether a must-pass scenario broke."""
    for name, rate in (
        ("Held-back", verdict.get("held_back_pass_rate")),
        ("Target", verdict.get("target_pass_rate")),
    ):
        if rate:
            said = f"{name} pass rate {percent(rate[0])} → {percent(rate[1])}."
            # Only a check that ran says whether one broke.
            status = _must_pass_status(verdict, tiers)
            if status == "failed":
                return f"{said} {_broke(verdict)}"
            if status == "passed":
                return f"{said} No must-pass scenario broke{_noise(verdict)}."
            return said
    return ""


class FixNames:
    """What a fix's scenarios and checks are called where a person reads them. Each is
    read once, when first needed; a key or a sub-goal's id is never one of them."""

    def __init__(self, fix_run: AgentFixRun) -> None:
        self._fix_run = fix_run

    @cached_property
    def scenarios(self) -> dict[str, str]:
        """Each scenario's name by its key: the inventory the engine is briefed with,
        which names the flaky protected cases too."""
        return {
            one.key: one.name.strip()
            for one in inventory(self._fix_run)
            if one.name.strip()
        }

    @cached_property
    def checks(self) -> dict[str, str]:
        """What each sub-goal checks, by its id, from the environment's catalogue."""
        return {
            name: str(entry.get("what") or "").strip().rstrip(".")
            for name, entry in environment_sub_goals(self._fix_run.environment).items()
            if str(entry.get("what") or "").strip()
        }


def must_pass_check(check: str, names: FixNames) -> str | None:
    """A must-pass check, sent as "scenario key: sub-goal", by its scenario's name and
    what it checks. None when the scenario has no name: its key is never shown."""
    key, _, sub_goal = str(check).partition(": ")
    scenario = names.scenarios.get(key)
    if scenario is None:
        return None
    what = names.checks.get(sub_goal)
    named = f"Must-pass scenario “{scenario}”"
    return f"{named}: its check “{what}”" if what else named


def regressions(checks: list[str], names: FixNames) -> list[str]:
    """The must-pass checks a candidate broke, each by its scenario's name and what it
    checks, each said once; the scenarios with no name are counted, never shown by key."""
    said = list(
        dict.fromkeys(
            one for one in (must_pass_check(check, names) for check in checks) if one
        )
    )
    unnamed = {
        str(check).partition(": ")[0]
        for check in checks
        if must_pass_check(check, names) is None
    }
    if unnamed:
        said.append(
            "A must-pass scenario"
            if len(unnamed) == 1
            else f"{len(unnamed)} must-pass scenarios"
        )
    return said


def flaky_protected(report: dict, names: FixNames) -> list[str]:
    """The protected scenarios the current version is flaky on, so the fix was not held
    to them: each once by its name, with what each check checks and how often it passed.
    A check with no words reads as a check; scenarios with no name are only counted."""
    rows: dict[str, list[dict]] = {}
    for row in report.get("flaky_protected") or []:
        rows.setdefault(str(row.get("key")), []).append(row)
    said, unnamed = [], 0
    for key, checks in rows.items():
        # The engine names a row from the same inventory, so its name is the fallback.
        name = names.scenarios.get(key) or next(
            (str(row["name"]).strip() for row in checks if row.get("name")), ""
        )
        if not name:
            unnamed += 1
            continue
        held = ", ".join(
            f"{_check_words(row, names)} passed {row.get('passed')} of"
            f" {row.get('runs')} runs"
            for row in checks
        )
        said.append(
            f"Protected scenario “{name}” is flaky on the current version, so this"
            f" epoch wasn't held to it: {held}."
        )
    # Two keys the inventory names alike, with the same checks, read the same: once.
    said = list(dict.fromkeys(said))
    if unnamed:
        scenarios = (
            "1 protected scenario is"
            if unnamed == 1
            else f"{unnamed} protected scenarios are"
        )
        it = "it" if unnamed == 1 else "them"
        said.append(
            f"{scenarios} flaky on the current version, so this epoch wasn't held to"
            f" {it}."
        )
    return said


def _check_words(row: dict, names: FixNames) -> str:
    what = names.checks.get(str(row.get("sub_goal")))
    return f"its check “{what}”" if what else "a check"


def caveats(
    candidate: AgentFixCandidate, review: EvalReview | None, names: FixNames
) -> list[str]:
    """What the checks could not hold the candidate to: the must-pass checks that dropped
    within normal noise, and for a winner, platform evals that did not finish."""
    verdict = candidate.verdict or {}
    named = [
        must_pass_check(check, names)
        for check in verdict.get("must_pass_within_noise") or []
    ]
    # Two checks of one scenario with no words for either read the same: said once.
    said = list(
        dict.fromkeys(
            f"{one} passed less often, within normal noise, so it was not counted."
            for one in named
            if one
        )
    )
    unnamed = named.count(None)
    if unnamed == 1:
        said.append(
            "A must-pass scenario passed less often, within normal noise, so it was"
            " not counted."
        )
    elif unnamed:
        said.append(
            f"{unnamed} must-pass checks passed less often, within normal noise, so"
            " they were not counted."
        )
    graded = (
        review is not None
        and candidate.outcome == AgentFixCandidate.Outcome.WINNER
        and review.grading == EvalGrading.INCOMPLETE
    )
    return [*said, EVALS_INCOMPLETE] if graded else said


def candidate_wording(
    candidate: AgentFixCandidate,
    tiers: list[VerdictTier],
    *,
    review: EvalReview | None,
    files: list[ChangedFile] | None,
    names: FixNames,
) -> CandidateWording:
    """Everything the page and the pull request say about the candidate, from its verdict,
    its fix's plan, the files of the patch that ships, the fix's eval review and its
    names."""
    verdict: dict[str, Any] = candidate.verdict or {}
    return CandidateWording(
        headline=headline(verdict, tiers),
        must_pass=must_pass_line(verdict, candidate.fix_run.plan or {}, tiers),
        must_pass_broken=_scenarios(verdict.get("must_pass_regressions")),
        changes=None if files is None else changes_line(files),
        caveats=caveats(candidate, review, names),
    )
