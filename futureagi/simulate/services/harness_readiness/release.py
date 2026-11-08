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
            else "passed" if started == ["passed", "passed"] else "pending"
        ),
        "resets": _result(checks.get("reset_equivalence")),
        **{name: _result(recorded.get(name)) for name in RELEASE_CHECKS[2:]},
    }
    status: Literal["ready", "checks_pending", "failed"] = (
        "failed"
        if "failed" in results.values()
        else "ready" if set(results.values()) == {"passed"} else "checks_pending"
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
