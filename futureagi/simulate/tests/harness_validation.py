"""Settling pairing validations in tests, as the gateway ends a validation job."""

from typing import Any

from simulate.models import (
    HarnessAssembledBundle,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import archives, assembly, validation


def running_validations(environment: Any = None) -> list[HostedHarnessJob]:
    """The validation jobs still running, of ``environment`` when given, oldest first."""
    jobs = HostedHarnessJob.no_workspace_objects.filter(
        payload__metadata__certify_only=True,
        state__in=validation._LIVE_STATES,
    )
    if environment is not None:
        jobs = jobs.filter(environment_id=environment.id)
    return list(jobs.order_by("created_at"))


def settle_validations(environment: Any = None, *, passed: bool = True) -> list[str]:
    """End every running validation (of ``environment`` when given): its pairing
    certified, its bundle stored, when ``passed``; else the agent failed its check. Then each
    acts on its outcome, as a waiting move does; a validation that starts another is
    settled too. Returns the outcomes in order."""
    outcomes: list[str] = []
    while jobs := running_validations(environment):
        for job in jobs:
            if passed:
                # The sealed bundle is stored, as a fill stores it.
                object_key = f"harness-bundles/{job.assembled_bundle_id}.tar.gz"
                archives.put_archive(
                    object_key,
                    archives.pack_archive(
                        {assembly.SEALED_MANIFEST: b"{}", assembly.CERTIFICATE: b"{}"}
                    ),
                )
                HarnessAssembledBundle._base_manager.filter(
                    id=job.assembled_bundle_id
                ).update(
                    state=HarnessAssembledBundle.State.READY, object_key=object_key
                )
                HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
                    state=HostedHarnessJob.State.COMPLETED
                )
            else:
                HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
                    state=HostedHarnessJob.State.FAILED,
                    failure={
                        "code": "authoring_runtime_validation_failed",
                        "message": "the agent did not start",
                    },
                )
            outcomes.append(validation.finish_validation(job.id))
    return outcomes


PROVED = {
    "format": 2,
    "holds": True,
    "proved": True,
    "assumed": [],
    "blocked_reason": None,
}


def prove_pair(
    environment: Any,
    environment_version: Any = None,
    suite_version: Any = None,
    *,
    keys: Any = None,
) -> None:
    """Every member of the suite proved on this pair (the defaults when absent), as a
    finished build or validation records it, so a run reads its cases as validated.
    With ``keys``, only those, and the pair reads partly proved."""
    from django.core.cache import cache
    from django.utils import timezone

    suite = suite_version or environment.suite_version
    partial = keys is not None
    if keys is None:
        keys = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=suite
        ).values_list("scenario_key", flat=True)
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=suite,
        environment_version=environment_version or environment.environment_version,
        defaults={
            "status": "partial" if partial else "passed",
            "proved_at": timezone.now(),
            "proofs": dict.fromkeys(keys, PROVED),
        },
    )
    cache.clear()


def prove_every_pair(environment: Any) -> None:
    """Every active suite version proved on every active environment version of the
    environment, as validations of each pairing would have recorded it."""
    from simulate.models import HarnessEnvironmentVersion, HarnessSuiteVersion

    suites = HarnessSuiteVersion._base_manager.filter(
        job_id=environment.id, status="active", deleted=False
    )
    versions = HarnessEnvironmentVersion._base_manager.filter(
        job_id=environment.id, status="active", deleted=False
    )
    for suite in suites:
        for version in versions:
            prove_pair(environment, version, suite)
