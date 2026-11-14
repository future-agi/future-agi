"""Each scenario's lifecycle on a pair of versions: Draft, Validated, Active or Archived.

Derived, never stored. Suite versions are immutable and proofs are per pair, so a stored state
would duplicate both and drift on every Save, rebuild and candidate promotion. The one stored fact
is archiving. A case runs only when it is Validated or Active on the run's own pair.
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

LifecycleState = Literal["draft", "validated", "active", "archived"]
LANE_CODES = frozenset({"proof_deferred", "solution_assumed", "control_not_run"})
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
    "archived": "Archived on {date}: {archive_reason}",
    "validation_unavailable": "Validation is not available on this installation; this case runs unvalidated.",
}
WARNING_TEXTS = {
    "solution_assumed": "Kept as written: {k} of {n} solution steps never ran, because {tools} have no endpoint here.",
    "proof_deferred": (
        "Kept as written: this agent's world is its own service; the reference solution can only run against it live."
    ),
    "control_not_run": "Kept as written: negative control {name} could not run, because {tools} have no endpoint here.",
    "legacy_no_negative_control": "Validated before negative controls existed; add one to confirm.",
}
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
    values = _Safe(why=why[0] if why else "", **extra)
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
    return REASON_TEXTS.get(code, code).format_map(values)


def warning_text(
    code: str | None, entry: Mapping[str, Any] | None = None
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
    )


def runnable(view: LifecycleView | None) -> bool:
    """Whether a case with this lifecycle may be picked for a run of its pair."""
    if view is None:
        return True
    return (
        view["state"] in ("validated", "active")
        or view["reason"] == "validation_unavailable"
    )


def lifecycle_counts(views: Iterable[LifecycleView]) -> dict[str, int]:
    counts = dict.fromkeys(
        (
            "draft",
            "validated",
            "active",
            "archived",
            "proved",
            "kept_as_written",
            "not_proved",
        ),
        0,
    )
    for view in views:
        counts[view["state"]] += 1
        if view["state"] == "archived":
            continue
        if view["warning"] in LANE_CODES:
            counts["kept_as_written"] += 1
        elif view["state"] in ("validated", "active"):
            counts["proved"] += 1
        else:
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
        if (
            isinstance(entry, Mapping)
            and entry.get("format") != 2
            and entry.get("holds") is True
        ):
            found[key] = entry
        elif entry is None and suite_id in certified:
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

    if suite_version is None or environment_version is None:
        return {}
    members = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version=suite_version
    )
    if keys is not None:
        members = members.filter(scenario_key__in=keys)
    contents = dict(members.values_list("scenario_key", "content"))
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
                view = _view(good, entry=entry, warning=blocked)
            elif blocked == "no_negative_control" and key in legacy:
                view = _view(
                    good, entry=entry, warning="legacy_no_negative_control", legacy=True
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
                _view(good, entry=entry, legacy=True)
                if entry.get("holds") is True
                else _view("draft", reason=_legacy_reason(entry), entry=entry)
            )
        elif key in legacy:
            view = _view(good, entry=legacy[key], legacy=True)
        elif (
            entry is not None and entry.get("blocked_reason") == "validation_incomplete"
        ):
            view = _view("draft", reason="validation_incomplete", entry=entry)
        elif unavailable:
            view = _view("draft", reason="validation_unavailable")
        else:
            view = _view("draft", reason="not_validated")
        view["sources"] = [dict(one) for one in resolved.get(key) or []]
        view["source_changed"] = changed.get(key, [])
        bad = next((one for one in view["sources"] if not one.get("resolved")), None)
        if bad is not None and view["state"] in ("validated", "active"):
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
        views[key] = view
    return views


def in_draft_view() -> LifecycleView:
    return _view("draft", reason="in_draft")


def source_changed(
    environment_version: HarnessEnvironmentVersion, contents: Mapping[str, Any]
) -> dict[str, list[str]]:
    """T21: for each key, the refs it cites that changed since the parent environment version."""
    return {}
