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
    if sealed:
        return sealed
    recorded = recorded_contract(version.documents)
    if recorded:
        return _reached_as_built(
            job, version, recorded, agent_version_id, suite_version_id
        )
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


def _reached_as_built(
    job: HostedHarnessJob,
    version: Any,
    recorded: dict[str, Any],
    agent_version_id: Any,
    suite_version_id: Any,
) -> dict[str, Any]:
    """A version's recorded contract, reaching each tool as the version it was made from runs it.

    How a tool is reached is a build's fact: a build can serve a tool the author's contract imports. An edit made on
    the author's contract (a rule, a description, one more tool) records that contract again, so in a version no
    build sealed, each tool whose authored entry the edit left alone is reached as its base runs it; a tool the edit
    added or changed keeps its own entry, and one it removed is gone. Only ``tool_entrypoints`` are carried."""
    documents = version.documents or {}
    parent_id = getattr(version, "parent_id", None)
    entries = recorded.get("tool_entrypoints")
    if (
        "run_contract" in documents
        or parent_id is None
        or not isinstance(entries, list)
    ):
        return recorded
    parent = type(version)._base_manager.filter(id=parent_id).first()
    authored = _by_tool((getattr(parent, "documents", None) or {}).get("contract"))
    if not authored:
        return recorded
    built = _by_tool(
        version_contract(
            job,
            parent,
            agent_version_id=agent_version_id,
            suite_version_id=suite_version_id,
        )
    )
    reached = [
        built[tool]
        if isinstance(one, dict)
        and (tool := one.get("tool")) in built
        and authored.get(tool) == one
        else one
        for one in entries
    ]
    return {**recorded, "tool_entrypoints": reached}


def _by_tool(contract: Any) -> dict[Any, dict[str, Any]]:
    """A contract's tool entries by the tool each is for."""
    entries = contract.get("tool_entrypoints") if isinstance(contract, dict) else None
    listed = entries if isinstance(entries, list) else []
    return {
        one["tool"]: one for one in listed if isinstance(one, dict) and one.get("tool")
    }


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
