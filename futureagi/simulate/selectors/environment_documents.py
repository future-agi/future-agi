"""An environment's contract and scenario keys, read from what the job row already holds."""

from __future__ import annotations

import re
from typing import Any

from simulate.models import HostedHarnessJob


def contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The contract snapshot, preferring the verified copy over the live one."""
    data = row_stage_output(job, "contract")
    return data if isinstance(data, dict) else {}


def recorded_contract(documents: Any) -> dict[str, Any]:
    """The contract a version's documents record it runs with: the one its build sealed (or the default's it
    keeps), else the authored one."""
    documents = documents if isinstance(documents, dict) else {}
    for field in ("run_contract", "contract"):
        contract = documents.get(field)
        if isinstance(contract, dict) and contract:
            return contract
    return {}


def version_contract(
    job: HostedHarnessJob,
    version: Any = None,
    *,
    agent_version_id: Any = None,
    suite_version_id: Any = None,
) -> dict[str, Any]:
    """The contract a run of an environment version uses: ``version``, else the environment's default version, run
    by the agent and suite named, else the environment's default ones.

    The one that pair's ready bundle was sealed with, else the one the version's documents record, else the
    default version's; the authoring snapshot only before the environment has a version. Reads the pair's bundle
    row, and the bundle once per bundle."""
    from simulate.services.harness_versioning.assembly import sealed_contract

    version = version if version is not None else job.environment_version
    if version is None:
        return contract_data(job)
    sealed = sealed_contract(
        version,
        agent_version_id=agent_version_id or job.agent_version_id,
        suite_version_id=suite_version_id or job.suite_version_id,
    )
    recorded = sealed or recorded_contract(version.documents)
    if recorded:
        return recorded
    # A draft or older version that recorded no contract runs as the default version would, never as the first
    # authoring snapshot.
    default = job.environment_version
    if default is not None and default.id != version.id:
        return version_contract(
            job,
            default,
            agent_version_id=agent_version_id,
            suite_version_id=suite_version_id,
        )
    return contract_data(job)


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
