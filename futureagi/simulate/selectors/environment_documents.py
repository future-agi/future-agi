"""An environment's contract and scenario keys, read from what the job row already holds."""

from __future__ import annotations

import re
from typing import Any

from simulate.models import HostedHarnessJob


def contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The contract snapshot, preferring the verified copy over the live one."""
    data = row_stage_output(job, "contract")
    return data if isinstance(data, dict) else {}


def row_stage_output(job: HostedHarnessJob, kind: str) -> Any:
    """One authoring snapshot from the prefetch, else the row's live copy; never issues a query."""
    for output in getattr(job, "row_outputs", None) or []:
        if output.kind == kind:
            return output.data
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == kind:
            return output.get("data")
    return None


def registration_key(value: Any) -> str:
    """The key the runner registers a scenario under: its name slugged to ASCII."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
