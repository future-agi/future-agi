"""Each scenario's lifecycle on a pair of versions: Draft, Validated, Active, Kept as written or Archived.

Derived, never stored. Suite versions are immutable and proofs are per pair, so a stored state
would duplicate both and drift on every Save, rebuild and candidate promotion. The one stored fact
is archiving. A case runs when it is Active (or Kept as written) on the default pair, and also when
it is Validated on a candidate pair, which is how a candidate is tried before it is saved.
Kept as written runs but never reads Validated or Active: no proof showed its checks hold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

from simulate.models import (
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.canonical_json import canonical_digest

LifecycleState = Literal["draft", "validated", "active", "kept_as_written", "archived"]
LANE_CODES = frozenset(
    {"proof_deferred", "solution_assumed", "control_not_run", "judged_only"}
)
DRAFT_CHANGES = frozenset({"added", "changed"})

REASON_TEXTS = {
    "not_validated": "Not validated on these versions yet.",
    "in_draft": "Unsaved change; validate and save to use it.",
    "not_ready": "The world does not hold what this case presumes: {why}",
    "not_solvable": "The reference solution does not pass the case's own checks: {why}",
    "vacuous": "Its checks pass when the agent does nothing.",
    "broken_check": "A check is broken, not failing: {why}",
    "weak_check": "A check passes when the agent does nothing: {why}",
    "credits_failed_attempt": "A check credits a call that failed: {why}",
    "too_strict": "A check fails a valid route the instructions allow: {why}",
    "unchecked": "No check notices a wrong value for: {why}",
    "validation_incomplete": "Validation ran out of time before this case; validate again.",
    "no_negative_control": "No negative control: add a wrong behaviour that this case's checks must fail.",
    "negative_control_passed": "The checks pass a wrong behaviour: {control} ({control_why}). Tighten {checks}.",
    "answer_leaked": 'What the {who} sees gives the answer away: {field} repeats "{excerpt}" from {grader_field}.',
    "source_unresolved": "A cited source is not in this environment: {kind} {ref}.",
    "policy_conflict": "Its expected outcome contradicts a cited rule: {why}",
    "apply_failed": "This change could not be applied: {why}. Fix it in the builder chat or discard it.",
    "archived": "Archived on {date}: {archive_reason}",
    "validation_unavailable": "Validation is not available on this installation; this case runs unvalidated.",
    "validation_failed": "The last validation of these versions failed: {failure}.",
    "validation_ended_on_our_side": "The last validation of these versions did not finish: {failure}.",
}
# A failed validation's ending, as `check_ending` reads it, by the reason its unproved cases give.
ENDING_REASONS = {
    "failed": "validation_failed",
    "unavailable": "validation_ended_on_our_side",
}
WARNING_TEXTS = {
    "solution_assumed": "Kept as written: {k} of {n} solution steps never ran, because {tools} have no endpoint here.",
    "proof_deferred": (
        "Kept as written: this agent's world is its own service; the reference solution can only run against it live."
    ),
    "control_not_run": "Kept as written: negative control {name} could not run, because {tools} have no endpoint here.",
    "legacy_no_negative_control": "Validated before negative controls existed; add one to confirm.",
    "judged_only": (
        "Kept as written: only a judge grades this case, and nothing has shown the judge fails a wrong behaviour."
    ),
    "evidence_not_captured": (
        "{check} reads {evidence}, which this agent does not give the harness; it will read Not measured."
    ),
}
EVIDENCE_LABELS = {"tool_calls": "the tool calls", "world_state": "the world's records"}
# The readiness row that says whether the agent gives the harness each kind of evidence.
EVIDENCE_ROWS = {"tool_calls": "tool_routing", "world_state": "state"}
UNCAPTURED_STATUSES = frozenset({"blocked", "unavailable"})
LEGACY_TEXT = "Validated before checks were recorded. Validate again to confirm."


class ProofSummary(TypedDict):
    proved: bool
    outcome: str | None
    assumed: list[str]
    controls: list[dict[str, Any]]
    leaks: list[dict[str, Any]]
    judged_only: bool
    proved_at: str | None


class LifecycleView(TypedDict):
    state: LifecycleState
    reason: str | None
    reason_text: str | None
    warning: str | None
    warning_text: str | None
    legacy: bool
    proof: ProofSummary | None
    sources: list[dict[str, Any]]
    source_changed: list[str]
    # The environment version the cited sources changed in, when any did.
    source_changed_version: int | None
    # Its id, for comparing the cited sources with the parent version's.
    source_changed_version_id: str | None
    # From the case's content on this pair: a must-pass case, and its negative controls as written.
    must_pass: bool
    negative_controls: list[dict[str, Any]]


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return ""


def reason_text(
    code: str | None, entry: Mapping[str, Any] | None = None, **extra: Any
) -> str | None:
    if code is None:
        return None
    entry = entry or {}
    why = str(entry.get("why") or "").strip().splitlines()
    items = [line.strip().removeprefix("- ") for line in why[1:] if line.strip().startswith("- ")]
    values = _Safe(why=items[0] if items else why[0] if why else "", **extra)
    if code == "negative_control_passed":
        passed = next(
            (
                one
                for one in entry.get("controls") or []
                if isinstance(one, Mapping) and one.get("failed") is False
            ),
            {},
        )
        values.update(
            control=passed.get("name", ""),
            control_why=passed.get("why", ""),
            checks=", ".join(passed.get("held") or []) or "the checks",
        )
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
    return REASON_TEXTS.get(code, code).format_map(values)


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
    if code == "control_not_run":
        values["tools"] = ", ".join(
            sorted({tool for one in controls for tool in one.get("assumed") or []})
        )
    return WARNING_TEXTS.get(code, code).format_map(values)


def proof_passes(entry: object) -> bool:
    from simulate.services.harness_versioning.versions import _proof_passes

    return _proof_passes(entry)


def _summary(entry: Mapping[str, Any] | None) -> ProofSummary | None:
    if not isinstance(entry, Mapping):
        return None
    return ProofSummary(
        proved=proof_passes(entry),
        outcome=entry.get("outcome"),
        assumed=list(entry.get("assumed") or []),
        controls=[
            {
                "name": one.get("name"),
                "why": one.get("why"),
                "failed": one.get("failed"),
                "assumed": one.get("assumed") or [],
            }
            for one in entry.get("controls") or []
            if isinstance(one, Mapping)
        ],
        leaks=[
            dict(one) for one in entry.get("leaks") or [] if isinstance(one, Mapping)
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


def runnable(view: LifecycleView | None, *, default_pair: bool = True) -> bool:
    """Whether a case with this lifecycle may be picked for a run of its pair.

    On the environment's saved (default) pair only Active and Kept as written cases run. On a
    candidate pair a Validated case runs too: that is how a candidate is tried before it is saved,
    and saving it is what makes those cases Active."""
    if view is None:
        return True
    allowed = (
        ("active", "kept_as_written")
        if default_pair
        else ("validated", "active", "kept_as_written")
    )
    return view["state"] in allowed or view["reason"] == "validation_unavailable"


def lifecycle_counts(views: Iterable[LifecycleView]) -> dict[str, int]:
    """Cases per state, and proved / kept as written / not proved over every case not archived.
    `kept_as_written` is both a state and the proof bucket, counted once."""
    counts = dict.fromkeys(
        (
            "draft",
            "validated",
            "active",
            "kept_as_written",
            "archived",
            "proved",
            "not_proved",
        ),
        0,
    )
    for view in views:
        counts[view["state"]] += 1
        # Only a proof that ran counts as proved (TH-8752): a lane code or a legacy proof
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
    versions = list(
        HarnessSuiteVersion._base_manager.filter(
            job_id=environment.id, lifecycle_legacy=True
        ).values_list("id", flat=True)
    )
    if not versions:
        return found
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
    for suite_id, key, content in HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id__in=versions, scenario_key__in=list(contents)
    ).values_list("suite_version_id", "scenario_key", "content"):
        if key in found and found[key] is not None:
            continue
        # Content carried unchanged from a legacy version on the same environment version inherits it.
        if suite_id != suite_version.id and canonical_digest(
            content or {}
        ) != digests.get(key):
            continue
        proofs = proofs_by_suite.get(suite_id) or {}
        entry = proofs.get(key) if isinstance(proofs, Mapping) else None
        if isinstance(entry, Mapping) and (
            entry.get("legacy_validated") is True
            or (entry.get("format") != 2 and entry.get("holds") is True)
        ):
            found[key] = entry
        elif suite_id in certified and (
            entry is None or entry.get("blocked_reason") == "validation_incomplete"
        ):
            # A `validation_incomplete` entry counts as no format-2 proof (section 4.15).
            found.setdefault(key, None)
    return found


def scenario_lifecycle(
    environment: HostedHarnessJob,
    *,
    suite_version: HarnessSuiteVersion | None,
    environment_version: HarnessEnvironmentVersion | None,
    keys: list[str] | None = None,
) -> dict[str, LifecycleView]:
    """Every member's lifecycle on this pair, keyed by scenario key; empty without a pair."""
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
    proofs = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=suite_version, environment_version=environment_version
        )
        .values_list("proofs", flat=True)
        .first()
    ) or {}
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
    unavailable = not validation.can_validate(environment, environment.agent_version_id)
    ended = _last_check_ending(environment, suite_version, environment_version)
    resolved = resolve(
        environment,
        environment_version,
        {
            key: list((content or {}).get("sources") or [])
            for key, content in contents.items()
        },
        stored={key: list(row.get("sources") or []) for key, row in rows.items()},
    )
    changed = source_changed(environment_version, contents)
    from simulate.services.hosted_harness_conversation import failed_changes

    failed = failed_changes(environment) if default else {}
    good: LifecycleState = "active" if default else "validated"
    views: dict[str, LifecycleView] = {}
    for key in contents:
        row = rows.get(key) or {}
        entry = proofs.get(key) if isinstance(proofs, Mapping) else None
        entry = entry if isinstance(entry, Mapping) else None
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
            if entry.get("proved") is True:
                view = _view(good, entry=entry)
            elif entry.get("kept_as_written") is True and blocked in LANE_CODES:
                view = _view("kept_as_written", entry=entry, warning=blocked)
            elif blocked == "no_negative_control" and key in legacy:
                view = _view(
                    "kept_as_written",
                    entry=entry,
                    warning="legacy_no_negative_control",
                    legacy=True,
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
        view["negative_controls"] = [
            dict(one)
            for one in content.get("negative_controls") or []
            if isinstance(one, Mapping)
        ]
        view["sources"] = [dict(one) for one in resolved.get(key) or []]
        view["source_changed"] = changed.get(key, [])
        if view["source_changed"]:
            view["source_changed_version"] = environment_version.version_number
            view["source_changed_version_id"] = str(environment_version.id)
        bad = next((one for one in view["sources"] if not one.get("resolved")), None)
        # The proof can already read source_unresolved; its text still names the source.
        if bad is not None and (
            view["state"] in ("validated", "active", "kept_as_written")
            or view.get("reason") == "source_unresolved"
        ):
            view.update(
                state="draft",
                reason="source_unresolved",
                reason_text=reason_text(
                    "source_unresolved",
                    entry,
                    kind=bad["kind"],
                    ref=bad["ref"] or bad["label"],
                ),
                warning=None,
                warning_text=None,
            )
        if default and key in failed and view["state"] != "archived":
            view.update(
                state="draft",
                reason="apply_failed",
                reason_text=reason_text("apply_failed", {"why": failed[key]}),
                warning=None,
                warning_text=None,
            )
        if (
            view["state"] in ("validated", "active", "kept_as_written")
            and not view["warning"]
        ):
            missing = _uncaptured_check(
                entry,
                uncaptured(environment, suite_version, environment_version, proofs),
            )
            if missing is not None:
                view["warning"] = "evidence_not_captured"
                view["warning_text"] = warning_text(
                    "evidence_not_captured",
                    entry,
                    check=missing[0],
                    evidence=EVIDENCE_LABELS[missing[1]],
                )
        views[key] = view
    return views


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
    except Exception:  # noqa: BLE001 - unreadable readiness warns about nothing
        return set()
    return {
        kind
        for kind, row in EVIDENCE_ROWS.items()
        if statuses.get(row) in UNCAPTURED_STATUSES
    }


WORKFLOW_KINDS = ("success", "refusal", "wrong_result")


def workflow_proof(
    views: Mapping[str, LifecycleView], contents: Mapping[str, Any]
) -> list[dict[str, Any]]:
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
    rows: list[dict[str, Any]] = []
    for name in sorted(critical or by_workflow):
        counts = dict.fromkeys(WORKFLOW_KINDS, 0)
        for key in by_workflow[name]:
            view = views.get(key)
            if view is None or view["state"] == "archived":
                continue
            proof = view["proof"] or {}
            if view["state"] in ("validated", "active"):
                if proof.get("outcome") == "no_change":
                    counts["refusal"] += 1
                else:
                    counts["success"] += 1
            if any(
                one.get("failed") is True and not one.get("assumed")
                for one in proof.get("controls") or []
            ):
                counts["wrong_result"] += 1
        rows.append(
            {
                "workflow": name,
                **counts,
                "missing": [kind for kind in WORKFLOW_KINDS if not counts[kind]],
            }
        )
    return rows


def workflow_proofs(environment: HostedHarnessJob) -> list[dict[str, Any]]:
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
        key = (metadata.get("hosted_harness_receipt") or {}).get("scenario_key")
        if key in must and metadata.get("harness_outcome_status") == "failed":
            failed.add(must[key])
    return sorted(failed)


def in_draft_view() -> LifecycleView:
    return _view("draft", reason="in_draft")


def _parent_documents(
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


def _rules_of(documents: Mapping[str, Any]) -> list[str]:
    contract = documents.get("contract")
    rules = contract.get("hard_constraints") if isinstance(contract, Mapping) else None
    return [str(one) for one in rules or []]


def _matched_rule(quote: str, rules: list[str]) -> str | None:
    from simulate.services.harness_scenario_sources import normalise

    wanted = normalise(quote)
    return next((rule for rule in rules if wanted and wanted in normalise(rule)), None)


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
) -> dict[str, list[str]]:
    """For each key, the rule and file refs it cites whose text changed from the parent version.

    A rule changed when no rule of this version holds its quote, or the rule holding it now reads
    differently; a rule that only moved index is not a change. A file changed when its digest
    differs; when either version recorded no digests, both texts are read and compared, and a file
    either side cannot read counts as changed, which errs toward revalidating.
    """
    parent = (
        parent_documents
        if parent_documents is not None
        else _parent_documents(environment_version)
    )
    if parent is None:
        return {}
    documents = environment_version.documents or {}
    new_rules, old_rules = _rules_of(documents), _rules_of(parent)
    new_files = documents.get("file_digests")
    old_files = parent.get("file_digests")
    by_text: dict[str, bool] = {}
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
                quote = str(source.get("quote") or "")
                now, before = (
                    _matched_rule(quote, new_rules),
                    _matched_rule(quote, old_rules),
                )
                if now is None or now != before:
                    refs.append(ref)
            elif source.get("kind") == "file":
                path = ref.removeprefix("file:").strip().lstrip("/")
                if not isinstance(new_files, Mapping) or not isinstance(
                    old_files, Mapping
                ):
                    if by_text.get(path, True):
                        refs.append(ref)
                elif _file_digest(path, new_files) != _file_digest(path, old_files):
                    refs.append(ref)
        if refs:
            changed[key] = refs
    return changed


def _files_differ_by_text(
    environment_version: HarnessEnvironmentVersion, contents: Mapping[str, Any]
) -> dict[str, bool]:
    """For each cited file, whether its text differs from the parent version's, read from both
    archives when a version recorded no digests. A file either side cannot read reads changed."""
    import hashlib

    from simulate.services.harness_scenario_sources import file_bodies

    paths = {
        str(source.get("ref") or "").removeprefix("file:").strip().lstrip("/")
        for content in contents.values()
        for source in (content or {}).get("sources") or []
        if isinstance(source, Mapping) and source.get("kind") == "file"
    } - {""}
    if not paths or environment_version.parent_id is None:
        return {}
    parent = HarnessEnvironmentVersion._base_manager.filter(
        id=environment_version.parent_id
    ).first()
    now, before = file_bodies(environment_version, paths), file_bodies(parent, paths)
    return {
        path: path not in now
        or path not in before
        or hashlib.sha256(now[path]).digest() != hashlib.sha256(before[path]).digest()
        for path in paths
    }


def source_changed(
    environment_version: HarnessEnvironmentVersion, contents: Mapping[str, Any]
) -> dict[str, list[str]]:
    """T21: for each key, the refs it cites that changed since the parent environment version."""
    return changed_refs(environment_version, contents)
