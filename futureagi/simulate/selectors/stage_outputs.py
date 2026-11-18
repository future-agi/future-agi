"""An environment build's authoring documents, read from its stage outputs."""

from __future__ import annotations

from typing import Any

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput


def stage_output(job: HostedHarnessJob, kind: str) -> Any:
    """One authoring document: the verified snapshot, else the live copy, else None."""
    normalized = (
        HostedHarnessStageOutput.no_workspace_objects.filter(
            job=job, kind=kind, deleted=False
        )
        .values_list("data", flat=True)
        .first()
    )
    if normalized is not None:
        return normalized
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == kind:
            return output.get("data")
    return None
