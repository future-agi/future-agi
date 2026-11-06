"""A pairing's run readiness: resolving the pairing, the report, a run's snapshot and
the re-check that validates the pairing again."""

from __future__ import annotations

from typing import Any, TypedDict

import structlog
from django.utils import timezone

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.models.harness_versioning import VersionStatus
from simulate.services.harness_readiness.catalogue import (
    AREAS,
    CATALOG_VERSION,
    CATALOGUE,
    RULES_VERSION,
    WORST_FIRST,
    AnswerView,
    AreaView,
    Pairing,
    PairingView,
    RowView,
    Status,
)
from simulate.services.harness_readiness.evidence import (
    certified,
    pair_bundle,
    read_evidence,
)
from simulate.services.harness_readiness.rules import (
    answer,
    failed_actions,
    rows,
    worst,
)
from simulate.services.harness_versioning import validation
from simulate.services.harness_versioning.read import (
    _agent_definition_id,
    in_workspace,
    require_versioning,
    version_refs,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    _load_ids,
    _not_pickable,
    _pin_ids,
    agent_version_pickable,
)

logger = structlog.get_logger(__name__)


class ReadinessReport(TypedDict):
    pairing: PairingView
    validation: validation.ValidationView
    answer: AnswerView
    areas: list[AreaView]
    rows: list[RowView]
    counts: dict[Status, int]
    catalog_version: str
    rules_version: str
    computed_at: str


def resolve_pairing(
    environment: HostedHarnessJob,
    *,
    agent_version_id: Any = None,
    environment_version_id: Any = None,
    suite_version_id: Any = None,
) -> Pairing:
    """The pairing these ids name; an absent id is the environment's default.

    Unlike a run's picks, an agent version whose preflight failed or has not finished
    is resolved: readiness reads it as its start being Blocked.

    Raises:
        HostedHarnessError: 404 ``versioning_disabled`` when the environment has no
            versions to pair; 422 ``version_not_pickable`` for a version that is not
            one of this environment's.
    """
    require_versioning()
    if (
        environment.environment_version_id is None
        or environment.suite_version_id is None
    ):
        raise HostedHarnessError(
            "versioning_disabled",
            "this environment has no versions yet",
            status_code=404,
        )
    defaults = _pin_ids(environment)
    picked = (agent_version_id, environment_version_id, suite_version_id)
    ids = tuple(pick or default for pick, default in zip(picked, defaults, strict=True))
    for piece, model, pick, default in (
        ("environment", HarnessEnvironmentVersion, ids[1], defaults[1]),
        ("suite", HarnessSuiteVersion, ids[2], defaults[2]),
    ):
        if (
            str(pick) != str(default)
            and not model.no_workspace_objects.filter(
                job=environment, id=pick, status=VersionStatus.ACTIVE
            ).exists()
        ):
            raise _not_pickable(piece)
    if ids[0] and str(ids[0]) != str(defaults[0]):
        _require_environments_agent(environment, ids[0])
    agent_version, environment_version, suite_version, runtime = _load_ids(
        environment, ids
    )
    return Pairing(
        environment=environment,
        agent_version=agent_version,
        environment_version=environment_version,
        suite_version=suite_version,
        runtime=runtime,
    )


def _require_environments_agent(environment: HostedHarnessJob, agent_id: Any) -> None:
    from simulate.services.harness_agent_versions import lineage_version_ids

    definition_id = _agent_definition_id(environment)
    found = (
        definition_id is not None
        and AgentVersion.no_workspace_objects.filter(
            in_workspace(environment),
            id=agent_id,
            organization_id=environment.organization_id,
            agent_definition_id=definition_id,
        ).exists()
    )
    allowed = lineage_version_ids(environment, definition_id) if found else None
    discarded = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=agent_id,
        preflight_status=HarnessAgentVersionRuntime.PreflightStatus.DISCARDED,
    ).exists()
    if not found or discarded or (allowed is not None and str(agent_id) not in allowed):
        raise _not_pickable("agent")


def _validation(
    pairing: Pairing, bundle: HarnessAssembledBundle | None
) -> validation.ValidationView:
    """Where the pairing's validation stands; a ready or failed bundle reads so even
    where validation cannot run now. Only a validation's failed check fails a pairing,
    as the run gate reads it: a bundle an ordinary run failed reads ``retry``."""
    job = (
        HostedHarnessJob.no_workspace_objects.filter(
            assembled_bundle_id=bundle.id, payload__metadata__certify_only=True
        )
        .order_by("-created_at")
        .first()
        if bundle is not None
        else None
    )
    error = (bundle.error or {}) if bundle is not None else {}
    failed = bundle is not None and bundle.state == HarnessAssembledBundle.State.FAILED
    if certified(bundle):
        state = "ready"
    elif validation.failed_check(bundle):
        state = "failed"
    elif failed:
        state = "retry"
    elif bundle is not None and validation.live_validation(bundle.id) is not None:
        state = "validating"
    elif pairing.agent_version is None or not validation.can_validate(
        pairing.environment, pairing.agent_version.id
    ):
        state = "unavailable"
    elif error.get("last_attempt"):
        state = "retry"
    else:
        state = "not_run"
    shown: dict[str, Any] | None = None
    if state == "failed":
        shown = {"code": error.get("code"), "reason": validation.failed_reason(error)}
        if "failed_actions" in error:
            shown["failed_actions"] = error["failed_actions"]
    elif state == "retry":
        shown = {
            "code": (error.get("last_attempt") or {}).get("code", error.get("code"))
        }
    metadata = ((job.payload or {}).get("metadata") or {}) if job else {}
    return {
        "state": state,
        "job_id": str(job.id) if job is not None else None,
        "purpose": metadata.get("validation_purpose"),
        "finished_at": (
            job.terminal_at.isoformat() if job and job.terminal_at else None
        ),
        "error": shown,
    }


def recheck(pairing: Pairing) -> validation.ValidationView:
    """Validate the pairing again; moves no default.

    A failed pairing is reset to pending first. ``unavailable`` when validation cannot
    run here, or the agent cannot start until its preflight passes.

    Raises:
        UsageLimitExceeded: the organization cannot spend on a build sandbox, which a
            validation runs in; a pairing already certified starts none.
    """
    from simulate.services.harness_usage import require_harness_authoring

    agent = pairing.agent_version
    if not certified(pair_bundle(pairing)):
        require_harness_authoring(str(pairing.environment.organization_id))
    started = (
        validation.start_validation(
            pairing.environment,
            agent,
            pairing.environment_version,
            pairing.suite_version,
            purpose="recheck",
        )
        if agent is not None and agent_version_pickable(pairing.environment, agent.id)
        else None
    )
    if started is None:
        return {
            "state": "unavailable",
            "job_id": None,
            "purpose": "recheck",
            "finished_at": None,
            "error": None,
        }
    return started.as_view()


def readiness_report(
    pairing: Pairing, *, bundle: HarnessAssembledBundle | None = None
) -> ReadinessReport:
    """The pairing's readiness, computed now from its evidence. No writes.

    ``bundle`` is the pairing's bundle when the caller holds it already (a run's own);
    otherwise it is looked up as a run of the pairing would.
    """
    bundle = bundle or pair_bundle(pairing)
    evidence = read_evidence(pairing, bundle)
    found = rows(pairing, evidence, bundle)
    view = _validation(pairing, bundle)
    refs = version_refs(
        pairing.agent_version, pairing.environment_version, pairing.suite_version
    )
    by_id = {row["id"]: row for row in found}
    areas: list[AreaView] = [
        {
            "id": area,
            "status": worst(by_id[one.id]["status"] for one in members),
            "rows": [one.id for one in members],
        }
        for area in AREAS
        if (members := [one for one in CATALOGUE if one.area == area])
    ]
    counts: dict[Status, int] = dict.fromkeys(WORST_FIRST, 0)
    for row in found:
        counts[row["status"]] += 1
    return {
        "pairing": {
            "agent_version": refs["agent"],
            "environment_version": refs["environment"],
            "suite_version": refs["suite"],
            "evidence": evidence.source,
        },
        "validation": view,
        "answer": answer(
            found,
            validating=view["state"] == "validating",
            failed=view["state"] == "failed",
            named=bool(failed_actions(bundle)),
        ),
        "areas": areas,
        "rows": found,
        "counts": counts,
        "catalog_version": CATALOG_VERSION,
        "rules_version": RULES_VERSION,
        "computed_at": timezone.now().isoformat(),
    }


def store_run_snapshot(
    run_id: Any,
    environment: HostedHarnessJob,
    *,
    agent_version: AgentVersion | None,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    runtime: HarnessAgentVersionRuntime | None,
    bundle: HarnessAssembledBundle | None,
) -> None:
    """Keep on a new run the readiness of the versions and bundle it is pinned to.

    Best effort, and called once the run is committed, outside any lock: reading the
    evidence may download an archive, and a run whose snapshot cannot be read keeps
    none rather than failing.
    """
    try:
        snapshot = readiness_report(
            Pairing(
                environment=environment,
                agent_version=agent_version,
                environment_version=environment_version,
                suite_version=suite_version,
                runtime=runtime,
            ),
            bundle=bundle,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=run_id).update(
            capability_snapshot=snapshot
        )
    except Exception:
        logger.exception("harness_run_snapshot_failed", run_id=str(run_id))
