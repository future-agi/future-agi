"""Each scenario's lifecycle on a pair of versions: Draft, Validated, Active, Kept as written or Archived.

Derived, never stored. Suite versions are immutable and proofs are per pair, so a stored state
would duplicate both and drift on every Save, rebuild and candidate promotion. The one stored fact
is archiving. A proved case is Active on saved versions and Validated on unsaved (candidate) ones;
only Active and Kept as written cases run. Kept as written runs but never reads Validated or
Active: no proof showed its checks hold. A case with no negative control is Draft.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict, cast

import structlog
from django.db.models import F

from simulate.constants.hosted_harness import SCENARIO_KEY
from simulate.models import (
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.harness_versioning import VersionStatus
from simulate.services.canonical_json import canonical_digest
from simulate.services.harness_scenario_needs import case_needs
from simulate.services.harness_scenario_sources import SourceView, bare_ref, file_path
from simulate.services.harness_versioning.versions import (
    OTHER_AGENTS,
    control_ran,
    legacy_verdict,
    proof_passes,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.types import LifecycleState

logger = structlog.get_logger(__name__)
# The states a run may pick: Active, and Kept as written, which runs labelled as not proved so a
# case the harness cannot prove (an agent-bound tool) can still be run. Tighten here if product asks.
RUNNABLE_STATES: frozenset[LifecycleState] = frozenset({"active", "kept_as_written"})
# Shown for a reason or a warning code this release has no words for: never the code itself.
UNKNOWN_REASON_TEXT = "Not validated on these versions: validate it again to see why."
UNKNOWN_WARNING_TEXT = "Kept as written: part of this case could not be checked here."
CONTROL_CALL_UNREAD_TEXT = (
    "The checks pass a wrong way of handling it: {control} ({control_why}). It calls {calls}, which no check reads: "
    "add a check that fails when that happens."
)
DRAFT_CHANGES = frozenset({"added", "changed"})

REASON_TEXTS = {
    "not_validated": "Not validated on these versions yet.",
    "in_draft": "Unsaved change; validate and save to use it.",
    "not_ready": "The starting records do not have what this case expects: {why}",
    "not_solvable": "The expected way of handling this case does not pass its own checks: {why}",
    "vacuous": "Its checks pass when the agent does nothing.",
    "broken_check": "A check itself is broken, rather than the agent failing it: {why}",
    "weak_check": "A check passes when the agent does nothing: {why}",
    "credits_failed_attempt": "A check counts a failed call as done: {why}",
    "too_strict": "A check fails a way of handling it that the instructions allow: {why}",
    "unchecked": "No check notices a wrong value for: {why}",
    "validation_incomplete": "Validation did not finish this case; validate again.",
    "no_negative_control": "No wrong-way example: add a wrong way of handling this case that its checks must fail.",
    "control_misses_case_goal": (
        "Its wrong-way examples only fail checks every scenario shares; add one that fails this case's own goal{goals}."
    ),
    "not_active": "Validated on these versions, which are not saved yet: save them to run it.",
    "proved_on_other_agent": "Validated with another agent version; validate it with this one to run it.",
    "broken_join": "Its starting records do not match up: {joins}. Fix the field or add the missing rows.",
    "seed_records_unread": "Its starting records could not be read just now, so it is held back. Try again in a few minutes.",
    "storage_unreadable": "The files it cites could not be read just now, so it is held back. Try again in a few minutes.",
    "proofs_unread": "The last validation's results could not be read, so it is held back. Validate these versions again.",
    "tool_not_on_agent": "Needs {tool}, which agent version {agent} doesn't have.",
    "tools_unread": "Agent version {agent}'s tools have not been read yet: it is held until they are, then validated again.",
    "negative_control_passed": "The checks pass a wrong way of handling it: {control} ({control_why}). Tighten {checks}.",
    "answer_leaked": 'What the {who} sees gives the answer away: {field} repeats "{excerpt}" from {grader_field}.',
    "source_unresolved": "A cited source is not in this environment: {kind} {ref}.",
    "policy_conflict": "Its expected outcome contradicts a cited rule: {why}",
    "apply_failed": "This change could not be applied: {why}. Fix it in the builder chat or discard it.",
    "archived": "Archived on {date}: {archive_reason}",
    "validation_unavailable": "Validation is not available on this installation; this case runs without it.",
    "validation_failed": "The last validation of these versions failed: {failure}.",
    "validation_ended_on_our_side": "The last validation of these versions did not finish: {failure}.",
}
# Who holds a case back, by its reason: our side (something could not be read, or validation could not finish or run),
# a check that has not been made on these versions yet, or a proof that found something wrong with the case. Every
# reason in REASON_TEXTS is here; an archived case is not held, so it names no one.
HELD_BY: dict[str, Literal["ours", "unproved", "proof_failed"] | None] = {
    **dict.fromkeys(
        (
            "seed_records_unread",
            "storage_unreadable",
            "proofs_unread",
            "validation_incomplete",
            "validation_ended_on_our_side",
            "tools_unread",
            "validation_unavailable",
        ),
        "ours",
    ),
    **dict.fromkeys(
        (
            "not_validated",
            "in_draft",
            "proved_on_other_agent",
            "validation_failed",
            "apply_failed",
            "not_active",
            "tool_not_on_agent",
        ),
        "unproved",
    ),
    **dict.fromkeys(
        (
            "not_ready",
            "not_solvable",
            "vacuous",
            "broken_check",
            "weak_check",
            "credits_failed_attempt",
            "too_strict",
            "unchecked",
            "no_negative_control",
            "control_misses_case_goal",
            "broken_join",
            "negative_control_passed",
            "answer_leaked",
            "source_unresolved",
            "policy_conflict",
        ),
        "proof_failed",
    ),
    "archived": None,
}
# A failed validation's ending, as `check_ending` reads it, by the reason its unproved cases give.
ENDING_REASONS = {
    "failed": "validation_failed",
    "unavailable": "validation_ended_on_our_side",
}
WARNING_TEXTS = {
    "solution_assumed": (
        "Kept as written: {k} of {n} steps of the expected handling could not be run here ({tools}), so they were "
        "not checked."
    ),
    "proof_deferred": (
        "Kept as written: this agent keeps its own data; the expected handling can only be checked against it live."
    ),
    "control_not_run": (
        "Kept as written: the wrong-way example {name} could not be run here ({tools}), so it was not checked."
    ),
    "judged_only": (
        "Kept as written: only an AI judge grades this case, and nothing has shown it fails a wrong answer."
    ),
    "no_negative_control": "Kept as written: no wrong-way example to prove the check can fail.",
    "control_misses_case_goal": (
        "Kept as written: its wrong-way examples only fail checks every scenario shares, so none proves this case's "
        "own check can fail."
    ),
    "evidence_not_captured": (
        "{check} reads {evidence}, which this agent does not share with the test; it will read Not measured."
    ),
    # A model judged the expected outcome may contradict a cited rule; ALK keeps the case running, as written.
    "policy_conflict": (
        "Kept as written: a model judged its expected outcome may contradict a cited rule ({why}); a judgment, not a "
        "proof."
    ),
    # ALK's own words (`recorded_note`) when it sends them.
    "recorded_not_run": "{recorded}: recorded, not run (no world effect).",
}
EVIDENCE_LABELS = {"tool_calls": "the tool calls", "world_state": "the world's records"}
# The readiness row that says whether the agent gives the harness each kind of evidence.
EVIDENCE_ROWS = {"tool_calls": "tool_routing", "world_state": "state"}
UNCAPTURED_STATUSES = frozenset({"blocked", "unavailable"})
LEGACY_TEXT = "Validated before checks were recorded. Validate again to confirm."


class ProofControl(TypedDict):
    """One negative control as the proof ran it: `failed` true when the checks failed it, as they should; null when
    it never ran (`ran` false)."""

    name: str | None
    why: str | None
    ran: bool
    failed: bool | None
    assumed: list[str]


class ProofLeak(TypedDict, total=False):
    """Grading material a simulated person or the agent could see: who, through which field, and an excerpt."""

    who: str
    field: str
    grader_field: str
    excerpt: str


class ControlStep(TypedDict, total=False):
    tool: str
    arguments: dict[str, Any]


class NegativeControl(TypedDict, total=False):
    """A wrong behaviour as the case writes it: the steps, and the checks that must fail it."""

    name: str
    why: str
    steps: list[ControlStep]
    must_fail: list[str]


class ProofSummary(TypedDict):
    proved: bool
    outcome: str | None
    assumed: list[str]
    controls: list[ProofControl]
    leaks: list[ProofLeak]
    judged_only: bool
    proved_at: str | None
    # The versions the proof was made with; the agent is null for a proof made before it was recorded.
    agent_version_id: NotRequired[str | None]
    environment_version_id: NotRequired[str]
    suite_version_id: NotRequired[str]


class LifecycleView(TypedDict):
    state: LifecycleState
    reason: str | None
    # Who holds it back, by its reason (`HELD_BY`); None when nothing does.
    held_by: Literal["ours", "unproved", "proof_failed"] | None
    reason_text: str | None
    warning: str | None
    warning_text: str | None
    legacy: bool
    proof: ProofSummary | None
    sources: list[SourceView]
    source_changed: list[str]
    # The environment version the cited sources changed in, when any did.
    source_changed_version: int | None
    # Its id, for comparing the cited sources with the parent version's.
    source_changed_version_id: str | None
    # From the case's content on this pair: a must-pass case, and its negative controls as written.
    must_pass: bool
    negative_controls: list[NegativeControl]


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return ""


def why_items(entry: Mapping[str, Any] | None) -> list[str]:
    """The listed items of a proof's reason: the harness writes its first line, then one `- item` per line."""
    lines = str((entry or {}).get("why") or "").strip().splitlines()
    return [
        line.strip().removeprefix("- ")
        for line in lines[1:]
        if line.strip().startswith("- ")
    ]


def _missing_tools(entry: Mapping[str, Any]) -> list[str]:
    """The tools a refused case's reference calls that the agent lacks: the list the harness writes on the proof, else
    (a proof from before it wrote one) the items of its reason."""
    named = entry.get("missing_tools")
    if isinstance(named, list) and (listed := [str(one) for one in named if one]):
        return listed
    return why_items(entry)


def _agent_label(agent_version_id: Any) -> str:
    """An agent version as people name it, `v4`; "this one" when its number is not known."""
    from simulate.models import AgentVersion
    from simulate.services.harness_versioning.read import _label

    number = (
        AgentVersion.no_workspace_objects.filter(id=agent_version_id)
        .values_list("version_number", flat=True)
        .first()
        if agent_version_id
        else None
    )
    return _label(number) or "this one"


def reason_text(
    code: str | None, entry: Mapping[str, Any] | None = None, **extra: Any
) -> str | None:
    if code is None:
        return None
    entry = entry or {}
    why = str(entry.get("why") or "").strip().splitlines()
    items = why_items(entry)
    values = _Safe(why=items[0] if items else why[0] if why else "", **extra)
    if code == "negative_control_passed":
        passed = next(
            (
                one
                for one in entry.get("controls") or []
                if isinstance(one, Mapping)
                and control_ran(one)
                and one.get("failed") is False
            ),
            {},
        )
        values.update(
            control=passed.get("name", ""),
            control_why=passed.get("why", ""),
            checks=", ".join(passed.get("held") or []) or "the checks",
        )
        # The tools the wrong way called that the reference never calls: no check reads what they did.
        unread = [str(one) for one in passed.get("unmatched") or [] if str(one).strip()]
        if unread:
            return CONTROL_CALL_UNREAD_TEXT.format_map(
                _Safe(values, calls=", ".join(unread))
            )
    elif code == "control_misses_case_goal":
        # The case's own goals, as the harness lists them; none named, the sentence ends at "goal".
        values["goals"] = f": {', '.join(items)}" if items else ""
    elif code == "answer_leaked":
        leak = next(
            (one for one in entry.get("leaks") or [] if isinstance(one, Mapping)), {}
        )
        values.update(
            {
                name: leak.get(name, "")
                for name in ("who", "field", "excerpt", "grader_field")
            }
        )
    if code == "source_unresolved" and not values.get("kind"):
        return "A cited source is not in this environment."
    template = REASON_TEXTS.get(code)
    return template.format_map(values) if template is not None else UNKNOWN_REASON_TEXT


def warning_text(
    code: str | None, entry: Mapping[str, Any] | None = None, **extra: Any
) -> str | None:
    if code is None:
        return None
    entry = entry or {}
    assumed = list(entry.get("assumed") or [])
    controls = [one for one in entry.get("controls") or [] if isinstance(one, Mapping)]
    values = _Safe(
        k=len(assumed),
        n=entry.get("solution_steps") or len(assumed),
        tools=", ".join(assumed),
        name=", ".join(str(one.get("name")) for one in controls),
        **extra,
    )
    if code == "policy_conflict":
        # As the held wording reads it: the first listed item, else the reason's first line.
        items, why = why_items(entry), str(entry.get("why") or "").strip().splitlines()
        values["why"] = items[0] if items else why[0] if why else ""
    if code == "control_not_run":
        assumed = sorted(
            {tool for one in controls for tool in one.get("assumed") or []}
        )
        values["tools"] = ", ".join(assumed)
    template = WARNING_TEXTS.get(code)
    return template.format_map(values) if template is not None else UNKNOWN_WARNING_TEXT


def _summary(entry: Mapping[str, Any] | None) -> ProofSummary | None:
    if not isinstance(entry, Mapping):
        return None
    return ProofSummary(
        proved=proof_passes(entry),
        outcome=entry.get("outcome"),
        assumed=list(entry.get("assumed") or []),
        controls=[
            ProofControl(
                name=one.get("name"),
                why=one.get("why"),
                ran=control_ran(one),
                # A control that never ran has no verdict to show.
                failed=one.get("failed") if control_ran(one) else None,
                assumed=list(one.get("assumed") or []),
            )
            for one in entry.get("controls") or []
            if isinstance(one, Mapping)
        ],
        # Stored as the harness wrote them; shown as they are.
        leaks=[
            cast(ProofLeak, dict(one))
            for one in entry.get("leaks") or []
            if isinstance(one, Mapping)
        ],
        judged_only=bool(entry.get("judged_only")),
        proved_at=entry.get("proved_at"),
    )


def _view(
    state: LifecycleState,
    *,
    reason: str | None = None,
    entry: Mapping[str, Any] | None = None,
    warning: str | None = None,
    legacy: bool = False,
    reason_extra: Mapping[str, Any] | None = None,
) -> LifecycleView:
    return LifecycleView(
        state=state,
        reason=reason,
        held_by=HELD_BY.get(reason) if reason else None,
        reason_text=reason_text(reason, entry, **(reason_extra or {})),
        warning=warning,
        warning_text=warning_text(warning, entry),
        legacy=legacy,
        proof=_summary(entry),
        sources=[],
        source_changed=[],
        source_changed_version=None,
        source_changed_version_id=None,
        must_pass=False,
        negative_controls=[],
    )


def runnable(view: LifecycleView | None) -> bool:
    """Whether a case with this lifecycle may be picked for a run of its pair: only Active (validated in a saved
    suite) and Kept as written cases run. A case Validated on unsaved versions waits until they are saved."""
    if view is None:
        return True
    return (
        view["state"] in RUNNABLE_STATES or view["reason"] == "validation_unavailable"
    )


def lifecycle_counts(views: Iterable[LifecycleView]) -> dict[str, int]:
    """Cases per state, and proved / kept as written / not proved over every case not archived.
    `kept_as_written` is both a state and the proof bucket, counted once. `legacy_without_control` counts the cases
    that run kept as written with no negative control: ALK's warning on a current proof, or a legacy proof whose case
    writes none; Validate and rebuild keep them running, with a warning."""
    counts = dict.fromkeys(
        (
            "draft",
            "validated",
            "active",
            "kept_as_written",
            "archived",
            "proved",
            "not_proved",
            "legacy_without_control",
        ),
        0,
    )
    for view in views:
        counts[view["state"]] += 1
        if view["state"] == "kept_as_written" and (
            view.get("warning") == "no_negative_control"
            or (view.get("legacy") and not view.get("negative_controls"))
        ):
            counts["legacy_without_control"] += 1
        # Only a proof that ran counts as proved: a lane code or a legacy proof
        # runs, kept as written.
        if view["state"] in ("validated", "active"):
            counts["proved"] += 1
        elif view["state"] == "draft":
            counts["not_proved"] += 1
    return counts


def _legacy_reason(entry: Mapping[str, Any]) -> str:
    if not entry.get("ready"):
        return "not_ready"
    if not entry.get("solvable"):
        return "not_solvable"
    return str(entry.get("blocked_reason") or "vacuous")


def _certified(
    environment: HostedHarnessJob, suite_version_id: Any, environment_version_id: Any
) -> bool:
    return HarnessAssembledBundle._base_manager.filter(
        job_id=environment.id,
        suite_version_id=suite_version_id,
        environment_version_id=environment_version_id,
        state=HarnessAssembledBundle.State.READY,
    ).exists()


def _legacy_keys(
    environment: HostedHarnessJob,
    suite_version: HarnessSuiteVersion,
    environment_version: HarnessEnvironmentVersion,
    contents: Mapping[str, Any],
) -> dict[str, Mapping[str, Any] | None]:
    """Keys validated by legacy on this pair, with the legacy proof entry (None when the legacy
    pairing was certified with no entry at all)."""
    found: dict[str, Mapping[str, Any] | None] = {}
    # Newest first: the latest legacy version holding the case unchanged decides.
    versions = list(
        HarnessSuiteVersion._base_manager.filter(
            job_id=environment.id, lifecycle_legacy=True
        )
        .order_by(F("version_number").desc(nulls_last=True), "-created_at")
        .values_list("id", flat=True)
    )
    if not versions:
        return found
    order = {suite_id: at for at, suite_id in enumerate(versions)}
    proofs_by_suite = dict(
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version_id__in=versions, environment_version_id=environment_version.id
        ).values_list("suite_version_id", "proofs")
    )
    certified = set(
        HarnessAssembledBundle._base_manager.filter(
            job_id=environment.id,
            suite_version_id__in=versions,
            environment_version_id=environment_version.id,
            state=HarnessAssembledBundle.State.READY,
        ).values_list("suite_version_id", flat=True)
    )
    digests = {
        key: canonical_digest(content or {}) for key, content in contents.items()
    }
    members = sorted(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id__in=versions, scenario_key__in=list(contents)
        ).values_list("suite_version_id", "scenario_key", "content"),
        key=lambda one: order[one[0]],
    )
    # Keys a format-2 proof decided: a re-proof that ran ends what legacy granted, whatever it found.
    decided: set[str] = set()
    for suite_id, key, content in members:
        if key in decided or (key in found and found[key] is not None):
            continue
        # Content carried unchanged from a legacy version on the same environment version inherits it.
        if suite_id != suite_version.id and canonical_digest(
            content or {}
        ) != digests.get(key):
            continue
        proofs = proofs_by_suite.get(suite_id) or {}
        entry = proofs.get(key) if isinstance(proofs, Mapping) else None
        verdict = legacy_verdict(entry, suite_id in certified)
        if verdict == "entry" and isinstance(entry, Mapping):
            found[key] = entry
        elif verdict == "certified":
            found.setdefault(key, None)
        elif verdict == "decided":
            decided.add(key)
    return found


def scenario_lifecycle(
    environment: HostedHarnessJob,
    *,
    suite_version: HarnessSuiteVersion | None,
    environment_version: HarnessEnvironmentVersion | None,
    keys: list[str] | None = None,
    agent_version_id: Any = None,
) -> dict[str, LifecycleView]:
    """Every member's lifecycle on this pair, keyed by scenario key; empty without a pair.

    A proof counts only for the versions it was made with: the suite and environment version its row is kept for,
    and the agent version its validation ran (``agent_version_id``, the environment's default when absent). A proof
    made with another agent version reads Draft until the case is validated with this one."""
    from simulate.services.harness_scenario_sources import resolve
    from simulate.services.harness_versioning import validation
    from simulate.services.harness_versioning.versions import suite_cases

    if suite_version is None or environment_version is None:
        return {}
    contents = {
        key: content
        for key, _, content in suite_cases(suite_version)
        if keys is None or key in keys
    }
    proofs, proved_with, proved_by = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=suite_version, environment_version=environment_version
        )
        .values_list("proofs", "job__agent_version_id", "job_id")
        .first()
    ) or ({}, None, None)
    proofs = proofs or {}
    agent = agent_version_id or environment.agent_version_id

    def other_agent(entry: Mapping[str, Any]) -> bool:
        """Whether this entry was proved with another agent version: its own stamp, else (an older entry) the row's
        job; one whose agent is not recorded is kept as it was."""
        made_with = entry.get("agent_version_id") or proved_with
        return (
            made_with is not None and agent is not None and str(made_with) != str(agent)
        )

    rows = {
        row["scenario_key"]: row
        for row in HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key__in=list(contents)
        ).values("scenario_key", "archived_at", "archive_reason", "sources")
    }
    legacy = _legacy_keys(environment, suite_version, environment_version, contents)
    default = (
        suite_version.id == environment.suite_version_id
        and environment_version.id == environment.environment_version_id
    )
    # Saved versions make an active suite; on unsaved (candidate) versions a proved case is Validated, not Active.
    saved = default or (
        suite_version.status == VersionStatus.ACTIVE
        and environment_version.status == VersionStatus.ACTIVE
    )
    # Whether the pairing's own agent can be validated here: a default agent that can tells nothing about it.
    unavailable = not validation.can_validate(
        environment, agent_version_id or environment.agent_version_id
    )
    ended = _last_check_ending(environment, suite_version, environment_version)
    resolved = resolve(
        environment,
        environment_version,
        {
            key: list((content or {}).get("sources") or [])
            for key, content in contents.items()
        },
        stored={key: list(row.get("sources") or []) for key, row in rows.items()},
        proved={
            key: list(entry.get("sources") or [])
            for key, entry in (proofs.items() if isinstance(proofs, Mapping) else ())
            if isinstance(entry, Mapping) and entry.get("format") == 2
        },
    )
    agent_tools = _agent_tool_names(environment, agent, environment_version)
    without_tools: dict[Any, bool] = {}
    # The tools on every path of each case's reference, read only when the agent's tools are.
    needed = (
        {
            one.key: one.tools
            for one in case_needs(
                environment_version=environment_version, suite_version=suite_version
            )
        }
        if agent_tools is not None and agent_tools[0] is not None
        else {}
    )
    unread: set[str] = set()
    changed = changed_refs(environment_version, contents, unread=unread)
    current_rules = rules_of(environment_version.documents or {})
    proofs_lost = _proofs_unread(environment, suite_version, environment_version)
    from simulate.services.hosted_harness_conversation import failed_changes

    failed = failed_changes(environment) if default else {}
    good: LifecycleState = "active" if saved else "validated"
    views: dict[str, LifecycleView] = {}
    not_captured: set[str] | None = None
    for key in contents:
        row: Mapping[str, Any] = rows.get(key) or {}
        entry = proofs.get(key) if isinstance(proofs, Mapping) else None
        entry = entry if isinstance(entry, Mapping) else None
        if entry is not None and other_agent(entry):
            # Proved since with another agent version: this one's own proof, when it made one, still stands.
            own = (entry.get(OTHER_AGENTS) or {}).get(str(agent))
            entry = own if isinstance(own, Mapping) else entry
        if row.get("archived_at") is not None:
            view = _view(
                "archived",
                reason="archived",
                entry=entry,
                reason_extra={
                    "date": f"{row['archived_at']:%d %b %Y}",
                    "archive_reason": row.get("archive_reason") or "no reason given",
                },
            )
        elif (
            entry is not None
            and entry.get("format") == 2
            and entry.get("blocked_reason") != "validation_incomplete"
        ):
            blocked = entry.get("blocked_reason")
            if other_agent(entry) and (
                entry.get("proved") is True or blocked == "tool_not_on_agent"
            ):
                # Found by another agent version: its tools say nothing about this one's.
                view = _view("draft", reason="proved_on_other_agent", entry=entry)
            elif entry.get("proved") is True:
                view = _view(good, entry=entry)
            elif entry.get("kept_as_written") is True:
                # ALK decides kept-as-written and writes it on the entry; its code only picks the words.
                view = _view("kept_as_written", entry=entry, warning=blocked)
            elif blocked == "tool_not_on_agent":
                # The harness refused it: the tools are its reason's list, the version the one it proved with.
                view = _view(
                    "draft",
                    reason="tool_not_on_agent",
                    entry=entry,
                    reason_extra={
                        "tool": ", ".join(_missing_tools(entry)),
                        "agent": _agent_label(
                            entry.get("agent_version_id") or proved_with or agent
                        ),
                    },
                )
            else:
                view = _view(
                    "draft", reason=str(blocked or "not_solvable"), entry=entry
                )
        elif (
            entry is not None
            and entry.get("format") != 2
            and entry.get("blocked_reason") is None
        ):
            # An older ALK image's proof: `holds` is read, marked legacy.
            view = (
                _view("kept_as_written", entry=entry, legacy=True)
                if entry.get("holds") is True
                else _view("draft", reason=_legacy_reason(entry), entry=entry)
            )
        elif key in legacy:
            view = _view("kept_as_written", entry=legacy[key], legacy=True)
        elif (
            entry is not None and entry.get("blocked_reason") == "validation_incomplete"
        ):
            view = _view("draft", reason="validation_incomplete", entry=entry)
        elif unavailable:
            view = _view("draft", reason="validation_unavailable")
        elif ended is not None:
            view = _view(
                "draft",
                reason=ENDING_REASONS[ended[0]],
                reason_extra={"failure": ended[1]},
            )
        else:
            view = _view("draft", reason="not_validated")
        content = contents.get(key) or {}
        view["must_pass"] = content.get("must_pass") is True
        # As the case writes them.
        view["negative_controls"] = [
            cast(NegativeControl, dict(one))
            for one in content.get("negative_controls") or []
            if isinstance(one, Mapping)
        ]
        view["sources"] = [SourceView(**one) for one in resolved.get(key) or []]
        # A rule source resolved only through the rule that replaced it still quotes the old words: Source changed
        # until the case cites the new rule, whatever proved it since.
        reworded = [
            one["ref"]
            for one in view["sources"]
            if one["kind"] == "rule"
            and one["resolved"]
            and matched_rule(one["quote"], current_rules) is None
        ]
        view["source_changed"] = list(
            dict.fromkeys(
                ([] if _checked_here(proofs, key) else changed.get(key, [])) + reworded
            )
        )
        if view["source_changed"]:
            view["source_changed_version"] = environment_version.version_number
            view["source_changed_version_id"] = str(environment_version.id)
        bad = next((one for one in view["sources"] if not one.get("resolved")), None)
        if bad is None and reworded and view.get("reason") == "source_unresolved":
            # Held only for a rule the contract records as reworded: never checked against the new rule yet.
            _demote(view, "not_validated", reason_text("not_validated"))
        # The proof can already read source_unresolved; its text still names the source.
        if bad is not None and (
            view["state"] in ("validated", "active", "kept_as_written")
            or view.get("reason") == "source_unresolved"
        ):
            _demote(
                view,
                "source_unresolved",
                reason_text(
                    "source_unresolved",
                    entry,
                    kind=bad["kind"],
                    # A rule by its words; any other source by what it names, never its prefix again.
                    ref=(
                        bad["label"]
                        if bad["kind"] == "rule"
                        else bare_ref(bad["kind"], bad["ref"]) or bad["label"]
                    ),
                ),
            )
        if view["state"] != "archived" and (proofs_lost or key in unread):
            # Unknown just now, never "not validated": the case is held until the read works.
            reason = "proofs_unread" if proofs_lost else "storage_unreadable"
            _demote(view, reason, reason_text(reason))
        if agent_tools is not None and view["state"] in RUNNABLE_STATES | {"validated"}:
            names, label = agent_tools
            if names is None:
                # Proved by a check that never had this agent's tools: unknown, never Active.
                made_by = (entry or {}).get("job_id") or proved_by
                if made_by not in without_tools:
                    without_tools[made_by] = _proved_without_tools(made_by, agent)
                if entry is not None and without_tools[made_by]:
                    _demote(
                        view, "tools_unread", reason_text("tools_unread", agent=label)
                    )
            elif lacking := sorted(needed.get(key, frozenset()) - names):
                _demote(
                    view,
                    "tool_not_on_agent",
                    reason_text(
                        "tool_not_on_agent", tool=", ".join(lacking), agent=label
                    ),
                )
        if default and key in failed and view["state"] != "archived":
            _demote(
                view,
                "apply_failed",
                reason_text("apply_failed", {"why": failed[key]}),
            )
        if (
            view["state"] in ("validated", "active", "kept_as_written")
            and not view["warning"]
        ):
            if not_captured is None:
                # The pair's readiness, read once for every case.
                not_captured = uncaptured(
                    environment, suite_version, environment_version, proofs
                )
            missing = _uncaptured_check(entry, not_captured)
            if missing is not None:
                view["warning"] = "evidence_not_captured"
                view["warning_text"] = warning_text(
                    "evidence_not_captured",
                    entry,
                    check=missing[0],
                    evidence=EVIDENCE_LABELS[missing[1]],
                )
        recorded = [str(one) for one in (entry or {}).get("recorded") or []]
        if (
            recorded
            and view["state"] in ("validated", "active", "kept_as_written")
            and not view["warning"]
        ):
            # Steps of tools with no world effect the proof recorded instead of running; the state stands.
            view["warning"] = "recorded_not_run"
            view["warning_text"] = str(
                (entry or {}).get("recorded_note") or ""
            ).strip() or warning_text(
                "recorded_not_run", entry, recorded=", ".join(recorded)
            )
        if view["proof"] is not None and entry is not None:
            made_with = entry.get("agent_version_id") or proved_with
            proof = view["proof"]
            proof["agent_version_id"] = str(made_with) if made_with else None
            proof["environment_version_id"] = str(environment_version.id)
            proof["suite_version_id"] = str(suite_version.id)
        views[key] = view
    return views


def _checked_here(proofs: Any, key: str) -> bool:
    """Whether a proof made on this pair reached the case: it read the case's sources against this environment
    version, so a source that changed from the parent version has been checked since. Any agent version's proof
    counts, since sources belong to the environment."""
    entry = proofs.get(key) if isinstance(proofs, Mapping) else None
    entries = [entry] if isinstance(entry, Mapping) else []
    if entries:
        entries += [
            one
            for one in (entries[0].get(OTHER_AGENTS) or {}).values()
            if isinstance(one, Mapping)
        ]
    return any(
        one.get("format") == 2 and one.get("blocked_reason") != "validation_incomplete"
        for one in entries
    )


# Reasons a case is held because something could not be read, never because a proof found anything.
UNREAD_REASONS = frozenset(
    {"seed_records_unread", "storage_unreadable", "proofs_unread"}
)


def _demote(view: LifecycleView, reason: str, text: str | None) -> None:
    """A case its proof would run, held back as Draft for a reason found after the proof."""
    view["state"] = "draft"
    view["reason"] = reason
    view["held_by"] = HELD_BY.get(reason)
    view["reason_text"] = text
    view["warning"] = None
    view["warning_text"] = None


def _last_check_ending(
    environment: HostedHarnessJob,
    suite_version: HarnessSuiteVersion,
    environment_version: HarnessEnvironmentVersion,
) -> tuple[Literal["unavailable", "failed"], str] | None:
    """How the newest validation of this pair ended, when it failed; a newer one, running or done, replaces it."""
    from simulate.services.harness_scenario_actions import check_ending

    last = (
        HostedHarnessJob.no_workspace_objects.filter(
            environment_id=environment.id,
            suite_version=suite_version,
            environment_version=environment_version,
            payload__metadata__certify_only=True,
        )
        .order_by("-created_at")
        .first()
    )
    if last is None or last.state != HostedHarnessJob.State.FAILED:
        return None
    return check_ending(last)


def _agent_tool_names(
    environment: HostedHarnessJob,
    agent_version_id: Any,
    environment_version: HarnessEnvironmentVersion,
) -> tuple[set[str] | None, str] | None:
    """The tools of the pairing's agent version, by name, and its label. ``None`` when nothing is compared: the
    agent the environment version was built with (its tools are the contract's the cases were written against), one
    whose tools no check can read, or one read as listing none. Names ``None`` when a check would read its tools and
    none has yet."""
    from simulate.models import AgentVersion, HarnessAgentVersionRuntime
    from simulate.services.agent_setup import stored_agent
    from simulate.services.harness_versioning.backfill import effective_runtime
    from simulate.services.harness_versioning.validation import needs_reading

    if agent_version_id is None or str(agent_version_id) == str(
        environment_version.agent_version_id
    ):
        return None
    agent = AgentVersion.no_workspace_objects.filter(id=agent_version_id).first()
    if agent is None:
        return None
    label = _agent_label(agent_version_id)
    document = stored_agent(agent_version_id)
    if document is None:
        runtime = effective_runtime(
            environment,
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=agent_version_id
            ).first(),
        )
        if not needs_reading(environment, agent, environment_version, runtime):
            return None
        return None, label
    names = {
        str(one.get("name"))
        for one in document.get("tools") or []
        if isinstance(one, Mapping) and one.get("name")
    }
    # A reading that lists no tool says nothing to compare against, as readiness reads it.
    return (names, label) if names else None


def _proved_without_tools(job_id: Any, agent_version_id: Any) -> bool:
    """Whether the check that made a proof ran this agent version without reading its tools; a proof that names no
    check, or one another agent's check made, is read as it always was."""
    if not job_id:
        return False
    made = (
        HostedHarnessJob.no_workspace_objects.filter(id=job_id)
        .values_list("agent_version_id", "payload__metadata")
        .first()
    )
    if made is None or str(made[0]) != str(agent_version_id):
        return False
    return isinstance(made[1], Mapping) and not made[1].get("agent_reading")


def _proofs_unread(
    environment: HostedHarnessJob,
    suite_version: HarnessSuiteVersion,
    environment_version: HarnessEnvironmentVersion,
) -> bool:
    """Whether the newest validation of this pair finished but its proofs could not be read: what it proved is
    unknown, so no earlier proof of the pair speaks for its cases."""
    last = (
        HostedHarnessJob.no_workspace_objects.filter(
            environment_id=environment.id,
            suite_version=suite_version,
            environment_version=environment_version,
            payload__metadata__certify_only=True,
        )
        .order_by("-created_at")
        .first()
    )
    return (
        last is not None
        and last.state == HostedHarnessJob.State.COMPLETED
        and ((last.payload or {}).get("metadata") or {}).get("scenario_proofs_recorded")
        is False
    )


def _uncaptured_check(
    entry: Mapping[str, Any] | None, missing: set[str]
) -> tuple[str, str] | None:
    """The first check of this proof that reads evidence the agent does not give, with that kind."""
    if (
        not missing
        or not isinstance(entry, Mapping)
        or not isinstance(entry.get("evidence"), Mapping)
    ):
        return None
    for check, kinds in entry["evidence"].items():
        for kind in kinds or []:
            if kind in missing and kind in EVIDENCE_LABELS:
                return str(check), str(kind)
    return None


def uncaptured(
    environment: HostedHarnessJob,
    suite_version: HarnessSuiteVersion,
    environment_version: HarnessEnvironmentVersion,
    proofs: Any,
) -> set[str]:
    """Evidence kinds this pair's readiness reads Blocked or Unavailable for: the agent does not give
    them to the harness. Read only when a proof records what its checks read (the report is cached);
    empty when readiness cannot be read."""
    if not isinstance(proofs, Mapping) or not any(
        isinstance(one, Mapping) and one.get("evidence") for one in proofs.values()
    ):
        return set()
    from simulate.services.harness_readiness.report import (
        readiness_report,
        resolve_pairing,
    )

    try:
        pairing = resolve_pairing(
            environment,
            environment_version_id=environment_version.id,
            suite_version_id=suite_version.id,
        )
        statuses = {
            row["id"]: row["status"] for row in readiness_report(pairing)["rows"]
        }
    except HostedHarnessError:
        # A pairing readiness cannot be read for (not pickable, versioning off) warns about nothing.
        logger.info("harness_uncaptured_readiness_unread", exc_info=True)
        return set()
    return {
        kind
        for kind, row in EVIDENCE_ROWS.items()
        if statuses.get(row) in UNCAPTURED_STATUSES
    }


WORKFLOW_KINDS = ("success", "refusal", "wrong_result")


class WorkflowProof(TypedDict):
    """Per release-critical workflow: proved cases per kind, and the kinds no case proves yet."""

    workflow: str
    success: int
    refusal: int
    wrong_result: int
    missing: list[str]


def workflow_proof(
    views: Mapping[str, LifecycleView], contents: Mapping[str, Any]
) -> list[WorkflowProof]:
    """Per release-critical workflow, how many cases prove a success, a refusal and a wrong result.

    A workflow is a `use_case` with at least one must-pass case; while no case is must-pass, every
    use case counts. A success or a refusal counts only a proved case (Validated or Active) whose
    reference changes, or leaves unchanged, the world; a wrong result is a case one of whose
    negative controls ran and failed. A control that did not run proves nothing."""
    by_workflow: dict[str, list[str]] = {}
    for key, content in contents.items():
        use_case = str((content or {}).get("use_case") or "").strip()
        if use_case:
            by_workflow.setdefault(use_case, []).append(key)
    critical = {
        name
        for name, keys in by_workflow.items()
        if any((contents.get(key) or {}).get("must_pass") is True for key in keys)
    }
    rows: list[WorkflowProof] = []
    for name in sorted(critical or by_workflow):
        counts = dict.fromkeys(WORKFLOW_KINDS, 0)
        for key in by_workflow[name]:
            view = views.get(key)
            if view is None or view["state"] == "archived":
                continue
            proof: Mapping[str, Any] = view["proof"] or {}
            if view["state"] in ("validated", "active"):
                if proof.get("outcome") == "no_change":
                    counts["refusal"] += 1
                else:
                    counts["success"] += 1
            if any(
                control_ran(one)
                and one.get("failed") is True
                and not one.get("assumed")
                for one in proof.get("controls") or []
            ):
                counts["wrong_result"] += 1
        rows.append(
            WorkflowProof(
                workflow=name,
                success=counts["success"],
                refusal=counts["refusal"],
                wrong_result=counts["wrong_result"],
                missing=[kind for kind in WORKFLOW_KINDS if not counts[kind]],
            )
        )
    return rows


def workflow_proofs(environment: HostedHarnessJob) -> list[WorkflowProof]:
    """`workflow_proof` over the environment's default pair; empty when it is not versioned."""
    if environment.suite_version is None or environment.environment_version is None:
        return []
    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    contents = dict(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).values_list("scenario_key", "content")
    )
    return workflow_proof(views, contents)


def must_pass_failures(execution: Any) -> list[str]:
    """Names of the must-pass cases of a run's suite version whose call in this run failed."""
    from simulate.models import CallExecution

    job = getattr(execution, "hosted_harness_job", None)
    if job is None or not getattr(job, "suite_version_id", None):
        return []
    must = {
        key: str((content or {}).get("name") or key)
        for key, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=job.suite_version_id
        ).values_list("scenario_key", "content")
        if isinstance(content, Mapping) and content.get("must_pass") is True
    }
    if not must:
        return []
    failed: set[str] = set()
    for metadata in CallExecution.objects.filter(test_execution=execution).values_list(
        "call_metadata", flat=True
    ):
        metadata = metadata if isinstance(metadata, Mapping) else {}
        # The case the call ran, as ingestion stores it: a receipt's `scenario_key` names the execution, not the case.
        # A registration's call stored before the key was written has none, and its receipt key is the case's own.
        key = metadata.get(SCENARIO_KEY) or (
            metadata.get("hosted_harness_receipt") or {}
        ).get("scenario_key")
        if key in must and metadata.get("harness_outcome_status") == "failed":
            failed.add(must[key])
    return sorted(failed)


def in_draft_view() -> LifecycleView:
    return _view("draft", reason="in_draft")


def archived_view(archived_at: datetime | None, archive_reason: str) -> LifecycleView:
    """An archived case's lifecycle: when, and the person's reason."""
    view = _view("archived", reason="archived")
    view["reason_text"] = reason_text(
        "archived",
        None,
        date=f"{archived_at:%d %b %Y}"
        if archived_at is not None
        else "an unknown date",
        archive_reason=archive_reason or "no reason given",
    )
    return view


def parent_version_documents(
    environment_version: HarnessEnvironmentVersion,
) -> Mapping[str, Any] | None:
    if environment_version.parent_id is None:
        return None
    parent = (
        HarnessEnvironmentVersion._base_manager.filter(id=environment_version.parent_id)
        .values_list("documents", flat=True)
        .first()
    )
    return parent if isinstance(parent, Mapping) else {}


def rules_of(documents: Mapping[str, Any]) -> list[str]:
    contract = documents.get("contract")
    rules = contract.get("hard_constraints") if isinstance(contract, Mapping) else None
    return [str(one) for one in rules or []]


def matched_rule(quote: str, rules: list[str]) -> str | None:
    from simulate.services.harness_scenario_sources import normalise

    wanted = normalise(quote)
    return next((rule for rule in rules if wanted and wanted in normalise(rule)), None)


def _replaced_by(
    before: str | None,
    new_rules: list[str],
    documents: Mapping[str, Any],
    parent_documents: Mapping[str, Any],
) -> str | None:
    """The rule the harness recorded as replacing ``before`` (`amendment_records`: the new record's `previous`, or the
    old record's `replaced_by`, by rule id), as this version holds it; None when it recorded none."""
    from simulate.services.harness_versioning.review_items import rule_key

    if not before:
        return None
    was = f"rule-{rule_key(before)}"
    records = [
        one
        for side in (documents, parent_documents)
        for one in ((side.get("contract") or {}).get("amendment_records") or [])
        if isinstance(one, Mapping) and one.get("kind") == "rule"
    ]
    now = next(
        (
            str(one.get("key"))
            for one in records
            if one.get("previous") == was and one.get("key")
        ),
        None,
    ) or next(
        (
            str(one.get("replaced_by"))
            for one in records
            if one.get("key") == was and one.get("replaced_by")
        ),
        None,
    )
    return next((rule for rule in new_rules if f"rule-{rule_key(rule)}" == now), None)


def _records(documents: Mapping[str, Any]) -> Any:
    return (documents.get("contract") or {}).get("amendment_records")


def rule_pair(
    quote: str,
    old_rules: list[str],
    new_rules: list[str],
    documents: Mapping[str, Any],
    parent_documents: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """The rule a quote names in the parent version and in this one: the rule holding its words, else the rule the
    recorded rewordings put in its place, so a case citing words older than both still reads the rule it became."""
    from simulate.services.harness_scenario_sources import replacement

    def held(rules: list[str], side: Mapping[str, Any]) -> str | None:
        at = replacement(quote, rules, _records(side))
        return matched_rule(quote, rules) or (rules[at] if at is not None else None)

    before = held(old_rules, parent_documents)
    # A cited rule this version no longer holds reads its replacement only as the harness declared it.
    after = held(new_rules, documents) or _replaced_by(
        before, new_rules, documents, parent_documents
    )
    if before is None and after is not None:
        # Words only this version holds: the parent's rule is the one this version recorded it replaced.
        before = _replaced(after, old_rules, documents)
    return before, after


def _replaced(
    rule: str, old_rules: list[str], documents: Mapping[str, Any]
) -> str | None:
    """The parent's rule that ``rule`` replaced, followed back through the rewordings this version recorded."""
    from simulate.services.harness_versioning.review_items import rule_key

    held = {f"rule-{rule_key(one)}": one for one in old_rules}
    records = [
        one
        for one in (_records(documents) or [])
        if isinstance(one, Mapping) and one.get("kind") == "rule"
    ]
    key, seen = f"rule-{rule_key(rule)}", set()
    while key and key not in seen:
        seen.add(key)
        key = next(
            (
                str(one["previous"])
                for one in records
                if one.get("key") == key and one.get("previous")
            ),
            "",
        )
        if key in held:
            return held[key]
    return None


def _file_digest(path: str, digests: Mapping[str, Any]) -> str | None:
    from simulate.services.harness_scenario_sources import bare_digest

    for candidate in (path, f"knowledge/{path}"):
        if candidate in digests:
            return bare_digest(digests[candidate])
    return None


def changed_refs(
    environment_version: HarnessEnvironmentVersion,
    contents: Mapping[str, Any],
    *,
    parent_documents: Mapping[str, Any] | None = None,
    unread: set[str] | None = None,
) -> dict[str, list[str]]:
    """For each key, the rule and file refs it cites whose text changed from the parent version.

    A rule changed when the rule its quote names (`rule_pair`: the rule holding its words, else the one the
    recorded rewordings put in its place) reads differently in the two versions; a rule that only moved index is not
    a change. A file changed when its digest
    differs; when either version recorded no digests, both texts are read and compared, and a file
    either side cannot read counts as changed, which errs toward revalidating; given ``unread``, its key is added
    there instead, as unknown.
    """
    parent = (
        parent_documents
        if parent_documents is not None
        else parent_version_documents(environment_version)
    )
    if parent is None:
        return {}
    documents = environment_version.documents or {}
    new_rules, old_rules = rules_of(documents), rules_of(parent)
    new_files = documents.get("file_digests")
    old_files = parent.get("file_digests")
    by_text: dict[str, bool | None] = {}
    if not isinstance(new_files, Mapping) or not isinstance(old_files, Mapping):
        by_text = _files_differ_by_text(environment_version, contents)
    changed: dict[str, list[str]] = {}
    for key, content in contents.items():
        refs: list[str] = []
        for source in (content or {}).get("sources") or []:
            if not isinstance(source, Mapping):
                continue
            ref = str(source.get("ref") or "")
            if source.get("kind") == "rule":
                before, now = rule_pair(
                    str(source.get("quote") or ""),
                    old_rules,
                    new_rules,
                    documents,
                    parent,
                )
                if now != before:
                    refs.append(ref)
            elif source.get("kind") == "file":
                path = file_path(ref)
                if not isinstance(new_files, Mapping) or not isinstance(
                    old_files, Mapping
                ):
                    differs = by_text.get(path, True)
                    if differs is None and unread is not None:
                        unread.add(key)
                    elif differs is not False:
                        refs.append(ref)
                elif _file_digest(path, new_files) != _file_digest(path, old_files):
                    refs.append(ref)
        if refs:
            changed[key] = refs
    return changed


def _files_differ_by_text(
    environment_version: HarnessEnvironmentVersion, contents: Mapping[str, Any]
) -> dict[str, bool | None]:
    """For each cited file, whether its text differs from the parent version's, read from both
    archives when a version recorded no digests. A file one side lacks reads changed; None when an archive could
    not be read just now."""
    import hashlib

    from simulate.services.harness_scenario_sources import read_files

    paths = {
        file_path(str(source.get("ref") or ""))
        for content in contents.values()
        for source in (content or {}).get("sources") or []
        if isinstance(source, Mapping) and source.get("kind") == "file"
    } - {""}
    if not paths or environment_version.parent_id is None:
        return {}
    parent = HarnessEnvironmentVersion._base_manager.filter(
        id=environment_version.parent_id
    ).first()
    (now, now_read), (before, before_read) = (
        read_files(environment_version, paths),
        read_files(parent, paths),
    )
    if not (now_read and before_read):
        return dict.fromkeys(paths)
    return {
        path: path not in now
        or path not in before
        or hashlib.sha256(now[path]).digest() != hashlib.sha256(before[path]).digest()
        for path in paths
    }


class DroppedCase(TypedDict):
    key: str
    name: str
    # The capabilities the case needed that the agent version no longer has, by kind (tools, sub-goals).
    missing: dict[str, list[str]]
    agent_version: str
    reason: str
    # Why restoring it did not prove it, when a rebuild brought it back unproved.
    restore_failed: str


class RestoredCase(TypedDict):
    key: str
    name: str
    # Proved again on the pair in view, with its agent version: only then does it run.
    proved: bool


def dropped_cases(
    suite_version: HarnessSuiteVersion | None,
    views: Mapping[str, LifecycleView],
) -> tuple[list[DroppedCase], list[RestoredCase]]:
    """The cases a rebuild dropped from ``suite_version`` for a capability the agent lost, with why; and the cases
    its parent had dropped that are back in it, with whether each was proved again (``views`` on the pair in view).
    Keys stay the same across both, so a case's history follows it."""
    if suite_version is None:
        return [], []
    held = (suite_version.documents or {}).get("dropped") or {}
    dropped: list[DroppedCase] = [
        {
            "key": str(key),
            "name": str(record.get("name") or key),
            # As the harness wrote it: names by kind.
            "missing": cast(dict[str, list[str]], dict(record.get("missing") or {})),
            "agent_version": str(record.get("agent_version") or ""),
            "reason": str(record.get("reason") or ""),
            "restore_failed": str(record.get("restore_failed") or ""),
        }
        for key, record in sorted(held.items())
        if isinstance(record, Mapping)
    ]
    parent = getattr(suite_version, "parent", None)
    before = (
        ((parent.documents or {}).get("dropped") or {}) if parent is not None else {}
    )
    members = {
        key: str((content or {}).get("name") or key)
        for key, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=suite_version, scenario_key__in=list(before)
        ).values_list("scenario_key", "content")
    }
    restored: list[RestoredCase] = [
        {
            "key": key,
            "name": name,
            "proved": (seen := views.get(key)) is not None
            and seen["state"] in ("active", "validated"),
        }
        for key, name in sorted(members.items())
        if key not in held
    ]
    return dropped, restored
