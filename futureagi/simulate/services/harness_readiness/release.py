"""An environment version's release checks, and the pairings certified on it.

A release is Ready only once all five release checks have passed: it starts, it resets,
the reference solution passes, a wrong answer fails and hidden checks don't leak. The
first two are read from the version's own build certificate; the other three from the
results a release step records on the version (``documents.release_checks``), pending
until it has. A pairing still needs its own readiness check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from django.core.cache import cache

from simulate.models import HarnessAssembledBundle, HarnessEnvironmentVersion

Result = Literal["passed", "failed", "pending"]
RELEASE_CHECKS = (
    "starts",
    "resets",
    "reference_passes",
    "wrong_answer_fails",
    "hidden_checks_kept",
)
# Recorded on the version by the release step; any other key is ignored.
_RECORDED = frozenset(RELEASE_CHECKS[2:])
HISTORY_PAGE = 50
# How long the harness version an organization's runs use now is remembered.
_HARNESS_VERSION_TTL_SECONDS = 600


class ReleaseChecks(TypedDict):
    status: Literal["ready", "checks_pending", "failed"]
    checks: dict[str, Result]


def _result(value: Any) -> Result:
    return value if value in ("passed", "failed") else "pending"


def release_checks(version: HarnessEnvironmentVersion) -> ReleaseChecks:
    """The five release checks of ``version``; ``ready`` only when all five passed."""
    return release_checks_for(version.documents or {})


def release_checks_for(documents: Mapping[str, Any]) -> ReleaseChecks:
    """As ``release_checks``, from a version's ``stage_outputs`` and
    ``release_checks`` documents."""
    certification = next(
        (
            one.get("data")
            for one in documents.get("stage_outputs") or []
            if isinstance(one, Mapping) and one.get("kind") == "certification"
        ),
        None,
    )
    certificate = (
        certification.get("certificate") if isinstance(certification, Mapping) else None
    )
    checks = certificate.get("checks") if isinstance(certificate, Mapping) else None
    checks = checks if isinstance(checks, Mapping) else {}
    started = [checks.get("static"), checks.get("processes")]
    recorded = documents.get("release_checks")
    recorded = recorded if isinstance(recorded, Mapping) else {}
    results: dict[str, Result] = {
        "starts": (
            "failed"
            if "failed" in started
            else "passed"
            if started == ["passed", "passed"]
            else "pending"
        ),
        "resets": _result(checks.get("reset_equivalence")),
        **{name: _result(recorded.get(name)) for name in RELEASE_CHECKS[2:]},
    }
    status: Literal["ready", "checks_pending", "failed"] = (
        "failed"
        if "failed" in results.values()
        else "ready"
        if set(results.values()) == {"passed"}
        else "checks_pending"
    )
    return {"status": status, "checks": results}


class HistoryItem(TypedDict):
    bundle_id: str
    agent_version: Any
    environment_version: Any
    suite_version: Any
    certified_at: str


class HistoryPage(TypedDict):
    items: list[HistoryItem]
    next: str | None


def certified_pairings(environment: Any, *, cursor: int | None) -> HistoryPage:
    """The pairings certified on ``environment``, newest first, with the exact agent,
    environment and suite versions each was certified for; ``cursor`` is the count of
    items already read."""
    from simulate.services.harness_versioning.read import version_refs

    start = cursor or 0
    rows = list(
        HarnessAssembledBundle._base_manager.filter(
            job_id=environment.id, state=HarnessAssembledBundle.State.READY
        )
        .exclude(object_key="")
        .select_related("agent_version", "environment_version", "suite_version")
        .order_by("-updated_at", "-id")[start : start + HISTORY_PAGE + 1]
    )
    items: list[HistoryItem] = []
    for bundle in rows[:HISTORY_PAGE]:
        refs = version_refs(
            bundle.agent_version, bundle.environment_version, bundle.suite_version
        )
        items.append(
            {
                "bundle_id": str(bundle.id),
                "agent_version": refs["agent"],
                "environment_version": refs["environment"],
                "suite_version": refs["suite"],
                "certified_at": bundle.updated_at.isoformat(),
            }
        )
    return {
        "items": items,
        "next": str(start + HISTORY_PAGE) if len(rows) > HISTORY_PAGE else None,
    }


def record_release_check(
    environment_version_id: Any,
    name: Literal["reference_passes", "wrong_answer_fails", "hidden_checks_kept"],
    result: Literal["passed", "failed"] | None,
    *,
    job_id: Any | None,
) -> None:
    """Set or remove one recorded release check on the version, never touching the others.

    ``None`` is pending: the key is removed. The job and time go in a sibling document the
    release checks ignore.
    """
    from django.db import transaction
    from django.utils import timezone

    with transaction.atomic():
        version = (
            HarnessEnvironmentVersion._base_manager.select_for_update()
            .filter(id=environment_version_id)
            .first()
        )
        if version is None:
            return
        documents = dict(version.documents or {})
        recorded = dict(documents.get("release_checks") or {})
        detail = dict(documents.get("release_checks_detail") or {})
        if result is None:
            recorded.pop(name, None)
            detail.pop(name, None)
        else:
            recorded[name] = result
            detail[name] = {
                "job_id": str(job_id) if job_id else None,
                "recorded_at": timezone.now().isoformat(),
            }
        documents["release_checks"] = recorded
        documents["release_checks_detail"] = detail
        version.documents = documents
        version.save(update_fields=["documents", "updated_at"])


def _proof_release_results(
    proofs: Mapping[str, Any], use_cases: Mapping[str, str]
) -> tuple[Result, Result]:
    """``reference_passes`` and ``wrong_answer_fails`` from one validation's proofs."""
    entries = [proofs.get(key) for key in use_cases]
    format_two = [
        one for one in entries if isinstance(one, Mapping) and one.get("format") == 2
    ]
    complete = bool(entries) and len(format_two) == len(entries)
    if any(one.get("ready") and not one.get("solvable") for one in format_two):
        reference: Result = "failed"
    elif complete and all(
        one.get("solvable") and not one.get("assumed") for one in format_two
    ):
        reference = "passed"
    else:
        reference = "pending"

    def ran(one: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [
            c
            for c in one.get("controls") or []
            if isinstance(c, Mapping) and not c.get("assumed")
        ]

    if any(c.get("failed") is False for one in format_two for c in ran(one)):
        wrong: Result = "failed"
    else:
        deterministic = [one for one in format_two if not one.get("judged_only")]
        every_control = complete and all(
            ran(one) and all(c.get("failed") for c in ran(one)) for one in deterministic
        )
        covered = True
        for use_case in set(use_cases.values()):
            outcomes = {
                (proofs.get(key) or {}).get("outcome")
                for key, case in use_cases.items()
                if case == use_case and (proofs.get(key) or {}).get("proved") is True
            }
            covered = covered and {"change", "no_change"} <= outcomes
        wrong = "passed" if every_control and covered else "pending"
    return reference, wrong


def record_proof_release_checks(environment_version: HarnessEnvironmentVersion) -> None:
    """Recompute ``reference_passes`` and ``wrong_answer_fails`` on the version, from its
    validation on the environment's default suite, else its newest one."""
    from simulate.models import HarnessSuiteMember, HarnessSuiteValidation

    validations = HarnessSuiteValidation.no_workspace_objects.filter(
        environment_version_id=environment_version.id
    )
    default_suite = getattr(environment_version.job, "suite_version_id", None)
    latest = (
        validations.filter(suite_version_id=default_suite).first()
        if default_suite
        else None
    ) or validations.order_by("-proved_at").first()
    if latest is None or not isinstance(latest.proofs, Mapping):
        return
    use_cases = {
        key: str((content or {}).get("use_case") or "")
        for key, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=latest.suite_version_id
        ).values_list("scenario_key", "content")
    }
    reference, wrong = _proof_release_results(latest.proofs, use_cases)
    for name, result in (
        ("reference_passes", reference),
        ("wrong_answer_fails", wrong),
    ):
        record_release_check(
            environment_version.id,
            name,
            None if result == "pending" else result,
            job_id=latest.job_id,
        )


def certificate_harness_version(certificate: Any) -> str | None:
    """The harness version a certificate records it was made with; ``None`` for one
    made before certificates recorded it."""
    runtime = certificate.get("runtime") if isinstance(certificate, Mapping) else None
    found = runtime.get("harness_version") if isinstance(runtime, Mapping) else None
    return str(found) if found else None


def current_harness_version(organization_id: Any) -> str | None:
    """The harness version the organization's runs use now: the one its newest
    certificate was made with, from any finished check or build. ``None`` until a
    certificate records one. Remembered for ten minutes."""
    key = f"harness-current-version:{organization_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    found = (
        HarnessAssembledBundle._base_manager.filter(
            organization_id=organization_id,
            state=HarnessAssembledBundle.State.READY,
            certificate__runtime__harness_version__isnull=False,
        )
        .order_by("-updated_at", "-id")
        .values_list("certificate__runtime__harness_version", flat=True)
        .first()
    )
    current = str(found) if found else None
    # An empty string remembers that none was found.
    cache.set(key, current or "", timeout=_HARNESS_VERSION_TTL_SECONDS)
    return current


def harness_outdated(certificate: Any, organization_id: Any) -> tuple[str, str] | None:
    """The harness version ``certificate`` was made with and the one runs use now,
    when they differ. Unknown on either side is not a change."""
    was = certificate_harness_version(certificate)
    now = current_harness_version(organization_id)
    if was is None or now is None or was == now:
        return None
    return was, now


def frameworks(certificate: Any) -> dict[str, str]:
    """The agent framework distributions a certificate records, with their versions."""
    runtime = certificate.get("runtime") if isinstance(certificate, Mapping) else None
    found = runtime.get("frameworks") if isinstance(runtime, Mapping) else None
    if not isinstance(found, Mapping):
        return {}
    return {str(name): str(version) for name, version in found.items() if version}


def framework_change(
    was: Mapping[str, str], now: Mapping[str, str]
) -> tuple[str, str, str] | None:
    """The first framework (by name) both certificates record at another version:
    its name, the version checked and the one now used."""
    for name in sorted(set(was) & set(now)):
        if was[name] != now[name]:
            return name, was[name], now[name]
    return None
