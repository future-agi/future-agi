"""Settling pairing validations in tests, as the gateway ends a validation job."""

from typing import Any

from simulate.models import HarnessAssembledBundle, HostedHarnessJob
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
    certified, its bundle stored, when ``passed``; else its check failed. Then each
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
                        "code": "certify_incomplete",
                        "message": "certify_incomplete",
                    },
                )
            outcomes.append(validation.finish_validation(job.id))
    return outcomes
